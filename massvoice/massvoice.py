import asyncio
import datetime
import logging
from typing import Union

import discord
from redbot.core import Config, app_commands, commands
from redbot.core.bot import Red

log = logging.getLogger("red.insanecogs.massvoice")

class MassVoiceUndoView(discord.ui.View):
    """
    A persistent view containing the 'Undo / Release' button.
    The custom_id is static ("massvoice:undo"), ensuring persistence.
    """
    def __init__(self, cog: "MassVoice"):
        super().__init__(timeout=None)
        self.cog = cog

    @discord.ui.button(
        label="Undo / Release",
        style=discord.ButtonStyle.danger,
        custom_id="massvoice:undo"
    )
    async def undo_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Callback for the undo button."""
        member = interaction.user
        
        # 1. Permission Check
        if not (member.guild_permissions.mute_members or member.guild_permissions.deafen_members):
            await interaction.response.send_message(
                "You do not have permission (Server Mute or Server Deafen) to perform this action.",
                ephemeral=True
            )
            return

        # Defer immediately since undoing takes time
        await interaction.response.defer()

        guild = interaction.guild
        message_id = str(interaction.message.id)

        # 2. Retrieve action details and flag as undone under lock
        async with self.cog.lock:
            async with self.cog.config.guild(guild).actions() as actions:
                action = actions.get(message_id)
                if not action:
                    await interaction.followup.send(
                        "Could not find the action record associated with this message.",
                        ephemeral=True
                    )
                    return

                if action.get("undone", False):
                    await interaction.followup.send(
                        "This action has already been undone.",
                        ephemeral=True
                    )
                    return

                # Mark as undone to abort any active background loops
                action["undone"] = True
                action["undone_by"] = member.id
                action_type = action.get("action_type")
                targets = action.get("targets", [])

        # 3. Process undo releases (outside the lock block to avoid blocking the background task)
        unmuted_count = 0
        undeafened_count = 0
        pending_count = 0
        tasks = []

        async with self.cog.config.guild(guild).pending_releases() as pending:
            for target_id in targets:
                target_member = guild.get_member(target_id)
                if not target_member:
                    try:
                        target_member = await guild.fetch_member(target_id)
                    except discord.HTTPException:
                        pass

                if not target_member:
                    continue

                if target_member.voice and target_member.voice.channel:
                    tasks.append((target_member, target_id))
                else:
                    member_id_str = str(target_id)
                    if member_id_str not in pending:
                        pending[member_id_str] = {"mute": False, "deafen": False}
                    pending[member_id_str][action_type] = True
                    pending_count += 1

            if tasks:
                edit_tasks = [
                    self.cog._edit_member(
                        m,
                        action_type,
                        False,
                        f"MassVoice Undo by {interaction.user.name}"
                    )
                    for m, _ in tasks
                ]
                results = await asyncio.gather(*edit_tasks)

                for idx, success in enumerate(results):
                    m, m_id = tasks[idx]
                    if success:
                        if action_type == "mute":
                            unmuted_count += 1
                        else:
                            undeafened_count += 1
                    else:
                        member_id_str = str(m_id)
                        if member_id_str not in pending:
                            pending[member_id_str] = {"mute": False, "deafen": False}
                        pending[member_id_str][action_type] = True
                        pending_count += 1

        # 4. Update the message UI
        button.disabled = True
        button.label = "Undone / Released"
        button.style = discord.ButtonStyle.secondary

        action_name = "Mute" if action_type == "mute" else "Deafen"
        restored_count = unmuted_count if action_type == "mute" else undeafened_count

        status_text = f"✅ **Mass {action_name} Undone**\n"
        status_text += f"- **Undone by**: {member.mention}\n"
        status_text += f"- **Restored immediately**: {restored_count} members\n"
        if pending_count > 0:
            status_text += f"- **Pending restore** (queued until they join voice): {pending_count} members"

        await interaction.message.edit(content=status_text, view=self)


class MassVoice(commands.Cog):
    """
    Mass mute or mass deafen all members in a voice channel with a persistent undo option.
    Includes background processing, real-time progress bars, and immediate cancellation.
    """

    def __init__(self, bot: Red):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=8923482, force_registration=True)
        self.lock = asyncio.Lock()  # Lock to serialize Config writes and prevent race conditions

        default_guild = {
            "actions": {},
            "pending_releases": {}
        }
        self.config.register_guild(**default_guild)

    async def cog_load(self):
        """Register the persistent view when the cog loads."""
        self.bot.add_view(MassVoiceUndoView(self))

    async def _check_permissions(self, ctx: commands.Context) -> bool:
        """Helper to verify if a user can issue mass voice commands."""
        if await self.bot.is_owner(ctx.author):
            return True
        perms = ctx.author.guild_permissions
        return perms.mute_members or perms.deafen_members

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
            elif action_type == "deafen":
                await member.edit(deafen=value, reason=reason)
            return True
        except (discord.Forbidden, discord.HTTPException):
            return False

    def _make_progress_bar(self, progress: int, total: int) -> str:
        """Generate a sleek unicode progress bar."""
        blocks = 10
        filled = int(round((progress / total) * blocks)) if total > 0 else 0
        empty = blocks - filled
        bar = "█" * filled + "░" * empty
        percent = int((progress / total) * 100) if total > 0 else 0
        return f"`{bar}` **{percent}%**"

    async def _run_mass_action(
        self,
        ctx: commands.Context,
        msg: discord.Message,
        channel: Union[discord.VoiceChannel, discord.StageChannel],
        targets: list,
        action_type: str,
        skipped_count: int
    ):
        """Background worker that applies the action and updates progress/aborts if requested."""
        reason = f"Mass {action_type.capitalize()} by {ctx.author.name}"
        total = len(targets)
        aborted = False

        for idx, member in enumerate(targets):
            # 1. Check if action has been aborted (undone) before performing edit
            async with self.lock:
                actions = await self.config.guild(ctx.guild).actions()
                action = actions.get(str(msg.id))
                if not action or action.get("undone", False):
                    aborted = True
                    break

            # 2. Perform voice state update
            success = await self._edit_member(member, action_type, True, reason)

            if success:
                # 3. Record success to DB under lock
                async with self.lock:
                    async with self.config.guild(ctx.guild).actions() as actions:
                        action = actions.get(str(msg.id))
                        if action:
                            if not action.get("undone", False):
                                # Action is still active, append user
                                action["targets"].append(member.id)
                            else:
                                # Action was cancelled DURING the edit call. Revert immediately.
                                await self._edit_member(member, action_type, False, "Mass action aborted.")
                                aborted = True
                                break

            # 4. Update the progress report in the public message
            progress = idx + 1
            bar = self._make_progress_bar(progress, total)
            emoji = "🎙️" if action_type == "mute" else "🔇"
            action_name = "Mute" if action_type == "mute" else "Deafen"

            content = (
                f"{emoji} **Mass {action_name} executing in {channel.mention}...**\n"
                f"- **Issued by**: {ctx.author.mention}\n"
                f"- **Progress**: {bar} ({progress}/{total} members)\n"
                f"- **Skipped**: {skipped_count}"
            )
            try:
                await msg.edit(content=content)
            except discord.HTTPException:
                pass

        # 5. Finalize status message
        async with self.lock:
            actions = await self.config.guild(ctx.guild).actions()
            action = actions.get(str(msg.id))
            is_undone = action.get("undone", False) if action else False

        if not is_undone and not aborted:
            # Completed normally
            emoji = "🎙️" if action_type == "mute" else "🔇"
            action_name = "Mute" if action_type == "mute" else "Deafen"
            content = (
                f"{emoji} **Mass {action_name} executed in {channel.mention}**\n"
                f"- **Issued by**: {ctx.author.mention}\n"
                f"- **Members affected**: {total}\n"
                f"- **Skipped**: {skipped_count}"
            )
            try:
                await msg.edit(content=content)
            except discord.HTTPException:
                pass

    @commands.hybrid_command(
        name="massmute",
        description="Mass mutes all members in a voice channel."
    )
    @commands.guild_only()
    @app_commands.describe(channel="The voice channel to mass mute. Defaults to your current channel.")
    async def massmute(self, ctx: commands.Context, channel: Union[discord.VoiceChannel, discord.StageChannel] = None):
        """Mass mutes everyone in a voice channel."""
        # 1. User permissions check
        if not await self._check_permissions(ctx):
            await self._send_private_error(ctx, "You do not have the required permissions (Server Mute or Server Deafen) to use this command.")
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

        # 4. Gather targets
        targets = []
        skipped_count = 0
        for member in channel.members:
            if member.bot:
                continue
            if not member.voice.mute:
                targets.append(member)
            else:
                skipped_count += 1

        if not targets:
            await self._send_private_error(
                ctx,
                f"No members in {channel.mention} need to be muted (they are already server muted or are bots)."
            )
            return

        # Send command receipt (ephemeral response)
        await ctx.send(f"Initiating mass mute for {len(targets)} members in {channel.mention}...", ephemeral=True)

        # 5. Post public progress message and view
        view = MassVoiceUndoView(self)
        bar = self._make_progress_bar(0, len(targets))
        content = (
            f"🎙️ **Mass Mute executing in {channel.mention}...**\n"
            f"- **Issued by**: {ctx.author.mention}\n"
            f"- **Progress**: {bar} (0/{len(targets)} members)\n"
            f"- **Skipped**: {skipped_count}"
        )
        msg = await ctx.channel.send(content=content, view=view)

        # 6. Initialize action details in Config
        async with self.lock:
            async with self.config.guild(ctx.guild).actions() as actions:
                actions[str(msg.id)] = {
                    "guild_id": ctx.guild.id,
                    "channel_id": channel.id,
                    "action_type": "mute",
                    "targets": [],
                    "undone": False,
                    "issuer_id": ctx.author.id,
                    "timestamp": datetime.datetime.utcnow().timestamp()
                }

        # 7. Spawn background task to process edits
        asyncio.create_task(
            self._run_mass_action(ctx, msg, channel, targets, "mute", skipped_count)
        )

    @commands.hybrid_command(
        name="massdeafen",
        description="Mass deafens all members in a voice channel."
    )
    @commands.guild_only()
    @app_commands.describe(channel="The voice channel to mass deafen. Defaults to your current channel.")
    async def massdeafen(self, ctx: commands.Context, channel: Union[discord.VoiceChannel, discord.StageChannel] = None):
        """Mass deafens everyone in a voice channel."""
        # 1. User permissions check
        if not await self._check_permissions(ctx):
            await self._send_private_error(ctx, "You do not have the required permissions (Server Mute or Server Deafen) to use this command.")
            return

        # 2. Bot permissions check
        if not ctx.guild.me.guild_permissions.deafen_members:
            await self._send_private_error(ctx, "I do not have the 'Deafen Members' permission on this server.")
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

        # 4. Gather targets
        targets = []
        skipped_count = 0
        for member in channel.members:
            if member.bot:
                continue
            if not member.voice.deafen:
                targets.append(member)
            else:
                skipped_count += 1

        if not targets:
            await self._send_private_error(
                ctx,
                f"No members in {channel.mention} need to be deafened (they are already server deafened or are bots)."
            )
            return

        # Send command receipt (ephemeral response)
        await ctx.send(f"Initiating mass deafen for {len(targets)} members in {channel.mention}...", ephemeral=True)

        # 5. Post public progress message and view
        view = MassVoiceUndoView(self)
        bar = self._make_progress_bar(0, len(targets))
        content = (
            f"🔇 **Mass Deafen executing in {channel.mention}...**\n"
            f"- **Issued by**: {ctx.author.mention}\n"
            f"- **Progress**: {bar} (0/{len(targets)} members)\n"
            f"- **Skipped**: {skipped_count}"
        )
        msg = await ctx.channel.send(content=content, view=view)

        # 6. Initialize action details in Config
        async with self.lock:
            async with self.config.guild(ctx.guild).actions() as actions:
                actions[str(msg.id)] = {
                    "guild_id": ctx.guild.id,
                    "channel_id": channel.id,
                    "action_type": "deafen",
                    "targets": [],
                    "undone": False,
                    "issuer_id": ctx.author.id,
                    "timestamp": datetime.datetime.utcnow().timestamp()
                }

        # 7. Spawn background task to process edits
        asyncio.create_task(
            self._run_mass_action(ctx, msg, channel, targets, "deafen", skipped_count)
        )

    @commands.Cog.listener()
    async def on_voice_state_update(self, member: discord.Member, before: discord.VoiceState, after: discord.VoiceState):
        """Event listener to apply pending unmute/undeafen releases when a member connects to voice."""
        if not after.channel:
            return

        guild = member.guild
        member_id_str = str(member.id)

        async with self.config.guild(guild).pending_releases() as pending:
            if member_id_str in pending:
                release_info = pending[member_id_str]
                mute_needed = release_info.get("mute", False)
                deafen_needed = release_info.get("deafen", False)

                kwargs = {}
                if mute_needed:
                    kwargs["mute"] = False
                if deafen_needed:
                    kwargs["deafen"] = False

                if kwargs:
                    try:
                        await member.edit(reason="MassVoice pending release applied.", **kwargs)
                        del pending[member_id_str]
                    except discord.Forbidden:
                        del pending[member_id_str]
                    except discord.HTTPException:
                        pass
