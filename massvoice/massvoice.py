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
    Because the custom_id is static ("massvoice:undo"), the view remains persistent
    across bot restarts when registered with the bot in `cog_load`.
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
        # 1. Permission check
        member = interaction.user
        if not (member.guild_permissions.mute_members or member.guild_permissions.deafen_members):
            await interaction.response.send_message(
                "You do not have permission (Server Mute or Server Deafen) to perform this action.",
                ephemeral=True
            )
            return

        # Defer immediately since modifying multiple members takes time
        await interaction.response.defer()

        guild = interaction.guild
        message_id = str(interaction.message.id)

        # 2. Retrieve action details
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

            # Mark as undone to prevent race conditions
            action["undone"] = True
            action["undone_by"] = member.id
            action_type = action.get("action_type")
            targets = action.get("targets", [])

        # 3. Process undo releases
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
                    # User is no longer in the guild, skip
                    continue

                if target_member.voice and target_member.voice.channel:
                    tasks.append((target_member, target_id))
                else:
                    # Queue for release when they next connect to voice
                    member_id_str = str(target_id)
                    if member_id_str not in pending:
                        pending[member_id_str] = {"mute": False, "deafen": False}
                    pending[member_id_str][action_type] = True
                    pending_count += 1

            if tasks:
                # Execute edits concurrently
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
                        # Queue if edit failed (e.g. they disconnected during the process)
                        member_id_str = str(m_id)
                        if member_id_str not in pending:
                            pending[member_id_str] = {"mute": False, "deafen": False}
                        pending[member_id_str][action_type] = True
                        pending_count += 1

        # 4. Disable button and update status message
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
    """

    def __init__(self, bot: Red):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=8923482, force_registration=True)
        
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
            await ctx.send("You do not have the required permissions (Server Mute or Server Deafen) to use this command.", ephemeral=True)
            return

        # 2. Bot permissions check
        if not ctx.guild.me.guild_permissions.mute_members:
            await ctx.send("I do not have the 'Mute Members' permission on this server.", ephemeral=True)
            return

        # 3. Resolve target channel
        if not channel:
            if ctx.author.voice and ctx.author.voice.channel:
                channel = ctx.author.voice.channel
            else:
                await ctx.send(
                    "You must be in a voice channel to use this command, or specify a voice channel as an argument.",
                    ephemeral=True
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
            await ctx.send(
                f"No members in {channel.mention} need to be muted (they are already server muted or are bots).",
                ephemeral=True
            )
            return

        # Inform command executor
        initial_msg = await ctx.send(f"Processing mass mute for {len(targets)} members in {channel.mention}...", ephemeral=True)

        # 5. Apply server mutes concurrently
        reason = f"Mass Muted by {ctx.author.name}"
        tasks = [self._edit_member(m, "mute", True, reason) for m in targets]
        results = await asyncio.gather(*tasks)
        
        success_targets = [targets[i].id for i, success in enumerate(results) if success]

        if not success_targets:
            await initial_msg.edit(content="Failed to mass mute any members in the channel (insufficient bot permissions or API errors).")
            return

        # 6. Post public control message & view
        view = MassVoiceUndoView(self)
        content = (
            f"🎙️ **Mass Mute executed in {channel.mention}**\n"
            f"- **Issued by**: {ctx.author.mention}\n"
            f"- **Members muted**: {len(success_targets)}\n"
            f"- **Skipped**: {skipped_count}"
        )
        msg = await ctx.channel.send(content=content, view=view)

        # 7. Save action details to Config
        async with self.config.guild(ctx.guild).actions() as actions:
            actions[str(msg.id)] = {
                "guild_id": ctx.guild.id,
                "channel_id": channel.id,
                "action_type": "mute",
                "targets": success_targets,
                "undone": False,
                "issuer_id": ctx.author.id,
                "timestamp": datetime.datetime.utcnow().timestamp()
            }

        # Clear ephemeral progress message
        try:
            await initial_msg.delete()
        except discord.HTTPException:
            pass

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
            await ctx.send("You do not have the required permissions (Server Mute or Server Deafen) to use this command.", ephemeral=True)
            return

        # 2. Bot permissions check
        if not ctx.guild.me.guild_permissions.deafen_members:
            await ctx.send("I do not have the 'Deafen Members' permission on this server.", ephemeral=True)
            return

        # 3. Resolve target channel
        if not channel:
            if ctx.author.voice and ctx.author.voice.channel:
                channel = ctx.author.voice.channel
            else:
                await ctx.send(
                    "You must be in a voice channel to use this command, or specify a voice channel as an argument.",
                    ephemeral=True
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
            await ctx.send(
                f"No members in {channel.mention} need to be deafened (they are already server deafened or are bots).",
                ephemeral=True
            )
            return

        # Inform command executor
        initial_msg = await ctx.send(f"Processing mass deafen for {len(targets)} members in {channel.mention}...", ephemeral=True)

        # 5. Apply server deafens concurrently
        reason = f"Mass Deafened by {ctx.author.name}"
        tasks = [self._edit_member(m, "deafen", True, reason) for m in targets]
        results = await asyncio.gather(*tasks)
        
        success_targets = [targets[i].id for i, success in enumerate(results) if success]

        if not success_targets:
            await initial_msg.edit(content="Failed to mass deafen any members in the channel (insufficient bot permissions or API errors).")
            return

        # 6. Post public control message & view
        view = MassVoiceUndoView(self)
        content = (
            f"🔇 **Mass Deafen executed in {channel.mention}**\n"
            f"- **Issued by**: {ctx.author.mention}\n"
            f"- **Members deafened**: {len(success_targets)}\n"
            f"- **Skipped**: {skipped_count}"
        )
        msg = await ctx.channel.send(content=content, view=view)

        # 7. Save action details to Config
        async with self.config.guild(ctx.guild).actions() as actions:
            actions[str(msg.id)] = {
                "guild_id": ctx.guild.id,
                "channel_id": channel.id,
                "action_type": "deafen",
                "targets": success_targets,
                "undone": False,
                "issuer_id": ctx.author.id,
                "timestamp": datetime.datetime.utcnow().timestamp()
            }

        # Clear ephemeral progress message
        try:
            await initial_msg.delete()
        except discord.HTTPException:
            pass

    @commands.Cog.listener()
    async def on_voice_state_update(self, member: discord.Member, before: discord.VoiceState, after: discord.VoiceState):
        """Event listener to apply pending unmute/undeafen releases when a member connects to voice."""
        # Only trigger when joining a voice channel
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
                        # Remove from pending on success
                        del pending[member_id_str]
                    except discord.Forbidden:
                        # Lack permissions, remove to avoid infinite loop of failures
                        del pending[member_id_str]
                    except discord.HTTPException:
                        # Retry next time if temporary API error or if they disconnected mid-process
                        pass
