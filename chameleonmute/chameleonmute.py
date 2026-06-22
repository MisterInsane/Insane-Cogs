import asyncio
import datetime
import logging
from typing import Union, Dict, Set, List

import discord
from redbot.core import Config, app_commands, commands
from redbot.core.bot import Red

log = logging.getLogger("red.insanecogs.chameleonmute")


class ChameleonCycle:
    """
    Represents an active recurring mute/unmute cycle for a guild.
    """
    def __init__(
        self,
        guild_id: int,
        channel_id: int,
        whistle_time: float,
        hold_time: float,
        mute_lead: float,
        message_id: int,
        task: asyncio.Task
    ):
        self.guild_id = guild_id
        self.channel_id = channel_id
        self.whistle_time = whistle_time
        self.hold_time = hold_time
        self.mute_lead = mute_lead
        self.message_id = message_id
        self.task = task
        self.muted_ids: List[int] = []  # Tracks members muted in the current phase (preserves order)


class ChameleonMuteView(discord.ui.View):
    """
    A persistent view containing the 'Stop / Release' button to cancel a cycle.
    The custom_id is static ("chameleonmute:stop") to ensure persistence.
    """
    def __init__(self, cog: "ChameleonMute"):
        super().__init__(timeout=None)
        self.cog = cog

    @discord.ui.button(
        label="Stop / Release",
        style=discord.ButtonStyle.danger,
        custom_id="chameleonmute:stop"
    )
    async def stop_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Callback for the stop button."""
        member = interaction.user
        guild = interaction.guild

        # 1. Permission Check
        if not member.guild_permissions.mute_members:
            await interaction.response.send_message(
                "You do not have permission (Mute Members) to stop this cycle.",
                ephemeral=True
            )
            return

        # Defer immediately since stopping/unmuting might take time due to rate limits
        await interaction.response.defer()

        # 2. Stop the cycle and release members
        await self.cog.stop_cycle(guild, stopped_by=member)

        # 3. Update the button UI
        button.disabled = True
        button.label = "Cycle Stopped"
        button.style = discord.ButtonStyle.secondary

        content = f"⏹️ **Chameleon Mute Cycle Stopped**\n- **Stopped by**: {member.mention}"
        try:
            await interaction.message.edit(content=content, view=self)
        except discord.HTTPException:
            pass


class ChameleonMute(commands.Cog):
    """
    Recurring mass mute and unmute cog for Meccha Chameleon games.
    Mutes players right before the whistle mark, holds them muted for a short duration, and unmutes them.
    """

    def __init__(self, bot: Red):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=9812493, force_registration=True)
        self.lock = asyncio.Lock()  # Serialize Config writes to prevent race conditions
        self.active_cycles: Dict[int, ChameleonCycle] = {}  # Tracks running cycles per guild

        default_guild = {
            "active_channel_id": None,
            "pending_releases": {}
        }
        self.config.register_guild(**default_guild)

    async def cog_load(self):
        """Register the persistent view when the cog loads."""
        self.bot.add_view(ChameleonMuteView(self))

    async def cog_unload(self):
        """Cancel all active cycles when the cog is unloaded."""
        for guild_id in list(self.active_cycles.keys()):
            guild = self.bot.get_guild(guild_id)
            if guild:
                # Run cleanup synchronously in unload or create task
                asyncio.create_task(self.stop_cycle(guild, stopped_by=None))

    async def _check_permissions(self, ctx: commands.Context) -> bool:
        """Helper to verify if a user can issue chameleon commands."""
        if await self.bot.is_owner(ctx.author):
            return True
        return ctx.author.guild_permissions.mute_members

    async def _send_private_error(self, ctx: commands.Context, text: str):
        """Send an error message that can only be seen by the invoking user (ephemeral or DM)."""
        if ctx.interaction:
            await ctx.send(text, ephemeral=True)
        else:
            try:
                await ctx.author.send(text)
            except discord.Forbidden:
                pass
            try:
                await ctx.message.delete()
            except discord.HTTPException:
                pass

    async def _edit_member(
        self,
        member: discord.Member,
        action_type: str,
        value: bool,
        reason: str
    ) -> bool:
        """Helper method to edit a member's voice state and catch errors."""
        try:
            if action_type == "mute":
                await member.edit(mute=value, reason=reason)
            return True
        except (discord.Forbidden, discord.HTTPException):
            return False

    async def _rate_limited_mute(self, guild: discord.Guild, channel: discord.VoiceChannel, cycle: ChameleonCycle, reason: str):
        """Mutes all players in the channel using a rate-limiting queue (10 actions per second)."""
        # Gather all non-bot, non-server-muted members in the channel
        targets = [m for m in channel.members if not m.bot and not m.voice.mute]
        if not targets:
            return

        tasks = []
        for member in targets:
            # Check if cycle was cancelled during execution
            if cycle.task.cancelled():
                break

            # Skip members who left the target channel
            if not member.voice or member.voice.channel != channel:
                continue

            # Start editing the member in the background
            task = asyncio.create_task(self._edit_member(member, "mute", True, reason))
            tasks.append(task)

            # Record that we muted this member
            cycle.muted_ids.append(member.id)

            # Rate limit: 10 actions per second (0.1s delay between initiating edits)
            await asyncio.sleep(0.1)

        # Wait for all edits to complete
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _rate_limited_unmute(self, guild: discord.Guild, cycle: ChameleonCycle, reason: str):
        """Unmutes all previously muted players in the same order using rate-limiting."""
        if not cycle.muted_ids:
            return

        tasks = []
        to_remove = []

        # Make a copy of the list to safely iterate and modify
        for member_id in list(cycle.muted_ids):
            member = guild.get_member(member_id)
            if not member:
                try:
                    member = await guild.fetch_member(member_id)
                except discord.HTTPException:
                    pass

            if member:
                # If they are still in a voice channel, we can unmute them
                if member.voice and member.voice.channel:
                    tasks.append(asyncio.create_task(self._edit_member(member, "mute", False, reason)))
                    to_remove.append(member_id)
                else:
                    # They left voice! Record in pending releases so they are unmuted when they rejoin
                    async with self.lock:
                        async with self.config.guild(guild).pending_releases() as pending:
                            pending[str(member_id)] = True
                    to_remove.append(member_id)
            else:
                to_remove.append(member_id)

            # Rate limit: 10 actions per second (0.1s delay between initiating edits)
            await asyncio.sleep(0.1)

        # Clean up successfully processed members from the tracking list
        for m_id in to_remove:
            if m_id in cycle.muted_ids:
                cycle.muted_ids.remove(m_id)

        # Wait for all edits to complete
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def stop_cycle(self, guild: discord.Guild, stopped_by: discord.Member = None) -> bool:
        """
        Stops the active cycle for a guild and releases all muted players.
        Includes a fallback to release players from config if the bot restarted.
        """
        cycle = self.active_cycles.get(guild.id)
        reason = f"ChameleonMute stopped by {stopped_by.name if stopped_by else 'system'}"

        if cycle:
            # 1. Cancel the background task
            cycle.task.cancel()
            self.active_cycles.pop(guild.id, None)

            # 2. Release currently muted players in this cycle
            await self._rate_limited_unmute(guild, cycle, reason)

        # 3. Fallback/Persistent Release: Check config for active channel or pending releases
        active_channel_id = await self.config.guild(guild).active_channel_id()
        if active_channel_id:
            channel = guild.get_channel(active_channel_id)
            if channel:
                # Unmute anyone in the channel who is currently server-muted
                muted_in_channel = [m for m in channel.members if not m.bot and m.voice.mute]
                if muted_in_channel:
                    tasks = [
                        self._edit_member(m, "mute", False, reason)
                        for m in muted_in_channel
                    ]
                    await asyncio.gather(*tasks, return_exceptions=True)

        # 4. Clear pending releases in config by attempting to unmute them immediately
        async with self.lock:
            async with self.config.guild(guild).pending_releases() as pending:
                pending_ids = list(pending.keys())
                for member_id_str in pending_ids:
                    member_id = int(member_id_str)
                    member = guild.get_member(member_id)
                    if not member:
                        try:
                            member = await guild.fetch_member(member_id)
                        except discord.HTTPException:
                            pass

                    if member and member.voice and member.voice.channel:
                        success = await self._edit_member(member, "mute", False, reason)
                        if success:
                            del pending[member_id_str]
                    else:
                        # If we can't unmute them immediately (not in voice), keep them in pending
                        pass

            # Clear the active channel tracking
            await self.config.guild(guild).active_channel_id.set(None)

        return True

    async def _cycle_loop(
        self,
        guild: discord.Guild,
        channel: discord.VoiceChannel,
        whistle_time: float,
        hold_time: float,
        mute_lead: float,
        msg: discord.Message
    ):
        """
        Background task running the recurring mute/unmute cycle.
        Uses dynamic lead time calculation to ensure players are muted exactly before the whistle.
        """
        cycle = self.active_cycles.get(guild.id)
        if not cycle:
            return

        start_time = asyncio.get_event_loop().time()
        next_whistle = start_time + whistle_time
        buffer = 0.5  # API latency buffer

        try:
            while True:
                # 1. Dynamic sleep until whistle lead time
                while True:
                    now = asyncio.get_event_loop().time()
                    # Recalculate lead time based on current channel population
                    targets = [m for m in channel.members if not m.bot and not m.voice.mute]
                    n = len(targets)
                    min_lead_time = (n * 0.1) + buffer
                    lead_time = max(mute_lead, min_lead_time)

                    target_time = next_whistle - lead_time
                    if now >= target_time:
                        break

                    # Sleep in small increments to adapt to members joining/leaving
                    sleep_duration = min(0.2, target_time - now)
                    if sleep_duration <= 0:
                        break
                    await asyncio.sleep(sleep_duration)

                # 2. Mute Phase
                mute_reason = f"Chameleon Whistle Mute (Whistle interval: {whistle_time}s)"
                await self._rate_limited_mute(guild, channel, cycle, mute_reason)

                # 3. Hold Phase
                now = asyncio.get_event_loop().time()
                hold_target_time = next_whistle + hold_time
                if hold_target_time > now:
                    await asyncio.sleep(hold_target_time - now)

                # 4. Unmute Phase
                unmute_reason = f"Chameleon Whistle Unmute (Hold duration: {hold_time}s)"
                await self._rate_limited_unmute(guild, cycle, unmute_reason)

                # 5. Prepare next cycle
                next_whistle += whistle_time

                # Update the message UI with a dynamic Discord timestamp countdown
                try:
                    now_ts = datetime.datetime.now().timestamp()
                    loop_time = asyncio.get_event_loop().time()
                    next_whistle_timestamp = int(now_ts + (next_whistle - loop_time))
                    
                    content = (
                        f"🔊 **Chameleon Mute Cycle Active in {channel.mention}**\n"
                        f"- **Whistle Interval**: {whistle_time}s\n"
                        f"- **Mute Lead Time**: {mute_lead}s\n"
                        f"- **Hold Time**: {hold_time}s\n"
                        f"- **Next Whistle**: <t:{next_whistle_timestamp}:R> (at <t:{next_whistle_timestamp}:T>)\n"
                    )
                    await msg.edit(content=content)
                except Exception:
                    pass

        except asyncio.CancelledError:
            log.info(f"Chameleon Mute cycle in guild {guild.id} has been cancelled.")
            raise
        except Exception as e:
            log.error(f"Error in Chameleon Mute cycle for guild {guild.id}: {e}", exc_info=True)
            # Safe unmute cleanup
            await self._rate_limited_unmute(guild, cycle, "Chameleon Whistle Error Release")
            self.active_cycles.pop(guild.id, None)

    @commands.hybrid_group(name="chameleonmute", description="Manage recurring Chameleon Mute cycles.")
    @commands.guild_only()
    async def chameleonmute(self, ctx: commands.Context):
        """Manage recurring Chameleon Mute cycles for Meccha Chameleon games."""
        pass

    @chameleonmute.command(name="start", description="Start a recurring mute/unmute cycle.")
    @app_commands.describe(
        whistle_time="The whistle interval in seconds.",
        hold_time="Duration to keep players muted in seconds (default 5.0).",
        mute_lead="Seconds before the whistle to start muting (default 5.0).",
        channel="Voice channel to use (defaults to your current channel)."
    )
    async def chameleonmute_start(
        self,
        ctx: commands.Context,
        whistle_time: int,
        hold_time: float = 5.0,
        mute_lead: float = 5.0,
        channel: Union[discord.VoiceChannel, discord.StageChannel] = None
    ):
        """Starts a recurring mass mute/unmute cycle in a voice channel."""
        # 1. User permissions check
        if not await self._check_permissions(ctx):
            await self._send_private_error(ctx, "You do not have the required permissions (Mute Members) to use this command.")
            return

        # 2. Bot permissions check
        if not ctx.guild.me.guild_permissions.mute_members:
            await self._send_private_error(ctx, "I do not have the 'Mute Members' permission on this server.")
            return

        # 3. Resolve target channel
        if not channel:
            if ctx.author.voice and ctx.author.voice.channel:
                channel = ctx.author.voice.channel
            else:
                await self._send_private_error(
                    ctx,
                    "You must be in a voice channel to use this command, or specify a voice channel as an argument."
                )
                return

        # 4. Prevent duplicate active cycles
        if ctx.guild.id in self.active_cycles:
            await self._send_private_error(ctx, "There is already an active Chameleon Mute cycle running in this guild. Please stop it first.")
            return

        # 5. Timing validation
        # Absolute minimum timing bounds
        if whistle_time <= mute_lead + hold_time + 1.0:
            await self._send_private_error(
                ctx,
                f"The whistle time ({whistle_time}s) must be longer than the sum of the mute lead time ({mute_lead}s) and hold time ({hold_time}s) to allow time for muting and unmuting."
            )
            return

        # Send command receipt (ephemeral response)
        await ctx.send(f"Initiating Chameleon Mute cycle for {channel.mention}...", ephemeral=True)

        # 6. Post public control message with the View
        next_whistle_timestamp = int(datetime.datetime.now().timestamp() + whistle_time)
        view = ChameleonMuteView(self)
        content = (
            f"🔊 **Chameleon Mute Cycle Active in {channel.mention}**\n"
            f"- **Whistle Interval**: {whistle_time}s\n"
            f"- **Mute Lead Time**: {mute_lead}s\n"
            f"- **Hold Time**: {hold_time}s\n"
            f"- **Next Whistle**: <t:{next_whistle_timestamp}:R> (at <t:{next_whistle_timestamp}:T>)\n"
        )
        msg = await ctx.channel.send(content=content, view=view)

        # 7. Store configuration for crash recovery/restart fail-safe
        async with self.lock:
            await self.config.guild(ctx.guild).active_channel_id.set(channel.id)

        # 8. Start background loop task
        loop = asyncio.get_running_loop()
        task = loop.create_task(self._cycle_loop(ctx.guild, channel, float(whistle_time), hold_time, mute_lead, msg))

        # 9. Track active cycle
        cycle = ChameleonCycle(
            guild_id=ctx.guild.id,
            channel_id=channel.id,
            whistle_time=float(whistle_time),
            hold_time=hold_time,
            mute_lead=mute_lead,
            message_id=msg.id,
            task=task
        )
        self.active_cycles[ctx.guild.id] = cycle

    @chameleonmute.command(name="stop", description="Stop the active recurring cycle.")
    async def chameleonmute_stop(self, ctx: commands.Context):
        """Stops the active Chameleon Mute cycle and unmutes all affected players."""
        # 1. User permissions check
        if not await self._check_permissions(ctx):
            await self._send_private_error(ctx, "You do not have the required permissions (Mute Members) to use this command.")
            return

        # 2. Stop the cycle
        stopped = await self.stop_cycle(ctx.guild, stopped_by=ctx.author)
        if stopped:
            await ctx.send("Chameleon Mute cycle stopped successfully.", ephemeral=True)
        else:
            await ctx.send("No active Chameleon Mute cycle was found to stop.", ephemeral=True)

    @commands.Cog.listener()
    async def on_voice_state_update(self, member: discord.Member, before: discord.VoiceState, after: discord.VoiceState):
        """Event listener to apply pending unmute releases when a member connects to voice."""
        if not after.channel:
            return

        guild = member.guild
        member_id_str = str(member.id)

        async with self.lock:
            async with self.config.guild(guild).pending_releases() as pending:
                if member_id_str in pending:
                    try:
                        await member.edit(mute=False, reason="ChameleonMute pending release applied.")
                        del pending[member_id_str]
                    except discord.Forbidden:
                        # Bot lacks permission, remove to prevent infinite loop / error spam
                        del pending[member_id_str]
                    except discord.HTTPException:
                        pass
