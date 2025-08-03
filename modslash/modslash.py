import discord
from redbot.core import commands, app_commands, Config
from redbot.core.bot import Red
from typing import List

# This check function is defined outside the class.
# It verifies if the user invoking the command is either the bot owner
# or has one of the roles configured in the cog's settings.
async def is_mod_check(interaction: discord.Interaction) -> bool:
    """Checks if the user has a configured moderator role or is the bot owner."""
    if await interaction.client.is_owner(interaction.user):
        return True
    
    cog = interaction.client.get_cog("ModSlash")
    if not cog:
        return False

    mod_role_ids = await cog.config.guild(interaction.guild).mod_roles()
    if not mod_role_ids:
        await interaction.response.send_message("No moderator roles have been configured on this server.", ephemeral=True)
        return False

    author_role_ids = {role.id for role in interaction.user.roles}
    
    if not author_role_ids.intersection(mod_role_ids):
        await interaction.response.send_message("You do not have the required role to use this command.", ephemeral=True)
        return False

    return True

# --- Context Menu Command Definitions (MUST be outside the class) ---

@app_commands.context_menu(name="Kick User")
@app_commands.default_permissions(kick_members=True)
@app_commands.check(is_mod_check)
async def kick_context_menu(interaction: discord.Interaction, member: discord.Member):
    """Kicks a user via the right-click context menu."""
    author = interaction.user
    bot = interaction.client
    reason = f"Kicked by {author.display_name} via context menu."

    if member.id == author.id:
        await interaction.response.send_message("You cannot kick yourself.", ephemeral=True)
        return

    if author.top_role <= member.top_role and not await bot.is_owner(author):
        await interaction.response.send_message("You cannot kick a member with an equal or higher role.", ephemeral=True)
        return

    if interaction.guild.me.top_role <= member.top_role:
        await interaction.response.send_message("I cannot kick a member with an equal or higher role than me.", ephemeral=True)
        return

    try:
        await member.kick(reason=reason)
        await interaction.response.send_message(f"Successfully kicked {member.mention}.", ephemeral=True)
    except discord.Forbidden:
        await interaction.response.send_message("I don't have the required permissions to kick this user.", ephemeral=True)
    except Exception as e:
        await interaction.response.send_message(f"An error occurred: {e}", ephemeral=True)

@app_commands.context_menu(name="Ban User")
@app_commands.default_permissions(ban_members=True)
@app_commands.check(is_mod_check)
async def ban_context_menu(interaction: discord.Interaction, member: discord.Member):
    """Bans a user via the right-click context menu."""
    author = interaction.user
    bot = interaction.client
    reason = f"Banned by {author.display_name} via context menu."

    if member.id == author.id:
        await interaction.response.send_message("You cannot ban yourself.", ephemeral=True)
        return

    if author.top_role <= member.top_role and not await bot.is_owner(author):
        await interaction.response.send_message("You cannot ban a member with an equal or higher role.", ephemeral=True)
        return

    if interaction.guild.me.top_role <= member.top_role:
        await interaction.response.send_message("I cannot ban a member with an equal or higher role than me.", ephemeral=True)
        return

    try:
        await member.ban(reason=reason)
        await interaction.response.send_message(f"Successfully banned {member.mention}.", ephemeral=True)
    except discord.Forbidden:
        await interaction.response.send_message("I don't have the required permissions to ban this user.", ephemeral=True)
    except Exception as e:
        await interaction.response.send_message(f"An error occurred: {e}", ephemeral=True)

@app_commands.context_menu(name="Mute User")
@app_commands.default_permissions(mute_members=True)
@app_commands.check(is_mod_check)
async def mute_context_menu(interaction: discord.Interaction, member: discord.Member):
    """Mutes a user via the right-click context menu."""
    author = interaction.user
    bot = interaction.client
    reason = f"Muted by {author.display_name} via context menu."

    if not member.voice or not member.voice.channel:
        await interaction.response.send_message(f"{member.mention} is not in a voice channel.", ephemeral=True)
        return

    if author.top_role <= member.top_role and not await bot.is_owner(author):
        await interaction.response.send_message("You cannot mute a member with an equal or higher role.", ephemeral=True)
        return

    try:
        await member.edit(mute=True, reason=reason)
        await interaction.response.send_message(f"Successfully muted {member.mention}.", ephemeral=True)
    except discord.Forbidden:
        await interaction.response.send_message("I don't have the required permissions to mute this user.", ephemeral=True)
    except Exception as e:
        await interaction.response.send_message(f"An error occurred: {e}", ephemeral=True)

@app_commands.context_menu(name="Deafen User")
@app_commands.default_permissions(deafen_members=True)
@app_commands.check(is_mod_check)
async def deafen_context_menu(interaction: discord.Interaction, member: discord.Member):
    """Deafens a user via the right-click context menu."""
    author = interaction.user
    bot = interaction.client
    reason = f"Deafened by {author.display_name} via context menu."

    if not member.voice or not member.voice.channel:
        await interaction.response.send_message(f"{member.mention} is not in a voice channel.", ephemeral=True)
        return

    if author.top_role <= member.top_role and not await bot.is_owner(author):
        await interaction.response.send_message("You cannot deafen a member with an equal or higher role.", ephemeral=True)
        return

    try:
        await member.edit(deafen=True, reason=reason)
        await interaction.response.send_message(f"Successfully deafened {member.mention}.", ephemeral=True)
    except discord.Forbidden:
        await interaction.response.send_message("I don't have the required permissions to deafen this user.", ephemeral=True)
    except Exception as e:
        await interaction.response.send_message(f"An error occurred: {e}", ephemeral=True)


class ModSlash(commands.Cog):
    """
    A cog for moderation commands that respects role hierarchy and has configurable moderator roles.
    Includes slash commands and context menu commands.
    """

    def __init__(self, bot: Red):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=5842647, force_registration=True)
        default_guild = {"mod_roles": []}
        self.config.register_guild(**default_guild)
        
        self.bot.tree.add_command(kick_context_menu)
        self.bot.tree.add_command(ban_context_menu)
        self.bot.tree.add_command(mute_context_menu)
        self.bot.tree.add_command(deafen_context_menu)

    async def cog_unload(self):
        """Clean up when the cog is unloaded."""
        self.bot.tree.remove_command(kick_context_menu.name, type=discord.AppCommandType.user)
        self.bot.tree.remove_command(ban_context_menu.name, type=discord.AppCommandType.user)
        self.bot.tree.remove_command(mute_context_menu.name, type=discord.AppCommandType.user)
        self.bot.tree.remove_command(deafen_context_menu.name, type=discord.AppCommandType.user)

    # --- Configuration Commands (Now as Prefix Commands) ---
    @commands.group()
    @commands.guild_only()
    @commands.has_permissions(manage_guild=True)
    async def modslashset(self, ctx: commands.Context):
        """Configuration for ModSlash commands."""
        pass

    @modslashset.command(name="addrole")
    async def add_mod_role(self, ctx: commands.Context, role: discord.Role):
        """Adds a role that can use ModSlash commands."""
        async with self.config.guild(ctx.guild).mod_roles() as mod_roles:
            if role.id in mod_roles:
                await ctx.send(f"{role.mention} is already a moderator role.")
            else:
                mod_roles.append(role.id)
                await ctx.send(f"{role.mention} has been added as a moderator role.")

    @modslashset.command(name="removerole")
    async def remove_mod_role(self, ctx: commands.Context, role: discord.Role):
        """Removes a role from the ModSlash moderators."""
        async with self.config.guild(ctx.guild).mod_roles() as mod_roles:
            if role.id not in mod_roles:
                await ctx.send(f"{role.mention} is not a moderator role.")
            else:
                mod_roles.remove(role.id)
                await ctx.send(f"{role.mention} has been removed from the moderator roles.")

    @modslashset.command(name="listroles")
    async def list_mod_roles(self, ctx: commands.Context):
        """Lists the roles that can use ModSlash commands."""
        mod_role_ids = await self.config.guild(ctx.guild).mod_roles()
        if not mod_role_ids:
            await ctx.send("No moderator roles are configured.")
            return

        role_mentions = [f"<@&{role_id}>" for role_id in mod_role_ids]
        await ctx.send(f"Moderator roles: {', '.join(role_mentions)}", allowed_mentions=discord.AllowedMentions.none())


    # --- Helper Functions ---
    async def _check_voice_channel(self, interaction: discord.Interaction, member: discord.Member) -> bool:
        if not member.voice or not member.voice.channel:
            await interaction.response.send_message(f"{member.mention} is not in a voice channel.", ephemeral=True)
            return False
        return True

    # --- Slash Commands ---
    @app_commands.command(name="kick", description="Kicks a user from the server.")
    @app_commands.default_permissions(kick_members=True)
    @app_commands.describe(member="The user to kick.", reason="The reason for the kick.")
    @app_commands.check(is_mod_check)
    async def kick_slash(self, interaction: discord.Interaction, member: discord.Member, reason: str = "No reason provided."):
        author = interaction.user
        if member.id == author.id:
            await interaction.response.send_message("You cannot kick yourself.", ephemeral=True)
            return

        if author.top_role <= member.top_role and not await self.bot.is_owner(author):
            await interaction.response.send_message("You cannot kick a member with an equal or higher role.", ephemeral=True)
            return

        if interaction.guild.me.top_role <= member.top_role:
            await interaction.response.send_message("I cannot kick a member with an equal or higher role than me.", ephemeral=True)
            return

        try:
            await member.kick(reason=f"Kicked by {author.name} ({author.id}). Reason: {reason}")
            await interaction.response.send_message(f"Successfully kicked {member.mention}. Reason: {reason}", ephemeral=True)
        except discord.Forbidden:
            await interaction.response.send_message("I don't have the required permissions to kick this user.", ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(f"An error occurred: {e}", ephemeral=True)

    @app_commands.command(name="ban", description="Bans a user from the server.")
    @app_commands.default_permissions(ban_members=True)
    @app_commands.describe(member="The user to ban.", reason="The reason for the ban.")
    @app_commands.check(is_mod_check)
    async def ban_slash(self, interaction: discord.Interaction, member: discord.Member, reason: str = "No reason provided."):
        author = interaction.user
        if member.id == author.id:
            await interaction.response.send_message("You cannot ban yourself.", ephemeral=True)
            return

        if author.top_role <= member.top_role and not await self.bot.is_owner(author):
            await interaction.response.send_message("You cannot ban a member with an equal or higher role.", ephemeral=True)
            return

        if interaction.guild.me.top_role <= member.top_role:
            await interaction.response.send_message("I cannot ban a member with an equal or higher role than me.", ephemeral=True)
            return

        try:
            await member.ban(reason=f"Banned by {author.name} ({author.id}). Reason: {reason}")
            await interaction.response.send_message(f"Successfully banned {member.mention}. Reason: {reason}", ephemeral=True)
        except discord.Forbidden:
            await interaction.response.send_message("I don't have the required permissions to ban this user.", ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(f"An error occurred: {e}", ephemeral=True)

    @app_commands.command(name="mute", description="Mutes a user in their voice channel.")
    @app_commands.default_permissions(mute_members=True)
    @app_commands.describe(member="The user to mute.", reason="The reason for the mute.")
    @app_commands.check(is_mod_check)
    async def mute_slash(self, interaction: discord.Interaction, member: discord.Member, reason: str = "No reason provided."):
        if not await self._check_voice_channel(interaction, member):
            return

        author = interaction.user
        if author.top_role <= member.top_role and not await self.bot.is_owner(author):
            await interaction.response.send_message("You cannot mute a member with an equal or higher role.", ephemeral=True)
            return

        try:
            await member.edit(mute=True, reason=f"Muted by {author.name} ({author.id}). Reason: {reason}")
            await interaction.response.send_message(f"Successfully muted {member.mention}. Reason: {reason}", ephemeral=True)
        except discord.Forbidden:
            await interaction.response.send_message("I don't have the required permissions to mute this user.", ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(f"An error occurred: {e}", ephemeral=True)

    @app_commands.command(name="unmute", description="Unmutes a user in their voice channel.")
    @app_commands.default_permissions(mute_members=True)
    @app_commands.describe(member="The user to unmute.")
    @app_commands.check(is_mod_check)
    async def unmute_slash(self, interaction: discord.Interaction, member: discord.Member):
        if not await self._check_voice_channel(interaction, member):
            return

        author = interaction.user
        if author.top_role <= member.top_role and not await self.bot.is_owner(author):
            await interaction.response.send_message("You cannot unmute a member with an equal or higher role.", ephemeral=True)
            return

        try:
            await member.edit(mute=False, reason=f"Unmuted by {author.name} ({author.id}).")
            await interaction.response.send_message(f"Successfully unmuted {member.mention}.", ephemeral=True)
        except discord.Forbidden:
            await interaction.response.send_message("I don't have the required permissions to unmute this user.", ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(f"An error occurred: {e}", ephemeral=True)

    @app_commands.command(name="deafen", description="Deafens a user in their voice channel.")
    @app_commands.default_permissions(deafen_members=True)
    @app_commands.describe(member="The user to deafen.", reason="The reason for the deafen.")
    @app_commands.check(is_mod_check)
    async def deafen_slash(self, interaction: discord.Interaction, member: discord.Member, reason: str = "No reason provided."):
        if not await self._check_voice_channel(interaction, member):
            return

        author = interaction.user
        if author.top_role <= member.top_role and not await self.bot.is_owner(author):
            await interaction.response.send_message("You cannot deafen a member with an equal or higher role.", ephemeral=True)
            return

        try:
            await member.edit(deafen=True, reason=f"Deafened by {author.name} ({author.id}). Reason: {reason}")
            await interaction.response.send_message(f"Successfully deafened {member.mention}. Reason: {reason}", ephemeral=True)
        except discord.Forbidden:
            await interaction.response.send_message("I don't have the required permissions to deafen this user.", ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(f"An error occurred: {e}", ephemeral=True)

    @app_commands.command(name="undeafen", description="Undeafens a user in their voice channel.")
    @app_commands.default_permissions(deafen_members=True)
    @app_commands.describe(member="The user to undeafen.")
    @app_commands.check(is_mod_check)
    async def undeafen_slash(self, interaction: discord.Interaction, member: discord.Member):
        if not await self._check_voice_channel(interaction, member):
            return
            
        author = interaction.user
        if author.top_role <= member.top_role and not await self.bot.is_owner(author):
            await interaction.response.send_message("You cannot undeafen a member with an equal or higher role.", ephemeral=True)
            return

        try:
            await member.edit(deafen=False, reason=f"Undeafened by {author.name} ({author.id}).")
            await interaction.response.send_message(f"Successfully undeafened {member.mention}.", ephemeral=True)
        except discord.Forbidden:
            await interaction.response.send_message("I don't have the required permissions to undeafen this user.", ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(f"An error occurred: {e}", ephemeral=True)

    @app_commands.command(name="silence", description="Mutes and deafens a user in their voice channel.")
    @app_commands.default_permissions(mute_members=True, deafen_members=True)
    @app_commands.describe(member="The user to silence.", reason="The reason for the silence.")
    @app_commands.check(is_mod_check)
    async def silence_slash(self, interaction: discord.Interaction, member: discord.Member, reason: str = "No reason provided."):
        if not await self._check_voice_channel(interaction, member):
            return

        author = interaction.user
        if author.top_role <= member.top_role and not await self.bot.is_owner(author):
            await interaction.response.send_message("You cannot silence a member with an equal or higher role.", ephemeral=True)
            return

        try:
            await member.edit(mute=True, deafen=True, reason=f"Silenced by {author.name} ({author.id}). Reason: {reason}")
            await interaction.response.send_message(f"Successfully silenced {member.mention}. Reason: {reason}", ephemeral=True)
        except discord.Forbidden:
            await interaction.response.send_message("I don't have the required permissions to silence this user.", ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(f"An error occurred: {e}", ephemeral=True)

    @app_commands.command(name="unsilence", description="Unmutes and undeafens a user in their voice channel.")
    @app_commands.default_permissions(mute_members=True, deafen_members=True)
    @app_commands.describe(member="The user to unsilence.")
    @app_commands.check(is_mod_check)
    async def unsilence_slash(self, interaction: discord.Interaction, member: discord.Member):
        if not await self._check_voice_channel(interaction, member):
            return

        author = interaction.user
        if author.top_role <= member.top_role and not await self.bot.is_owner(author):
            await interaction.response.send_message("You cannot unsilence a member with an equal or higher role.", ephemeral=True)
            return

        try:
            await member.edit(mute=False, deafen=False, reason=f"Unsilenced by {author.name} ({author.id}).")
            await interaction.response.send_message(f"Successfully unsilenced {member.mention}.", ephemeral=True)
        except discord.Forbidden:
            await interaction.response.send_message("I don't have the required permissions to unsilence this user.", ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(f"An error occurred: {e}", ephemeral=True)
