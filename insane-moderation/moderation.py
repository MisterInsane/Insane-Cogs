import discord
from redbot.core import commands, app_commands, Config
from redbot.core.bot import Red
from typing import List

# This check function is defined outside the class.
# It verifies if the user invoking the command is either the bot owner
# or has one of the roles configured in the cog's settings.
async def is_mod_check(interaction: discord.Interaction) -> bool:
    """Checks if the user has a configured moderator role or is the bot owner."""
    # Bot owner bypasses all checks
    if await interaction.client.is_owner(interaction.user):
        return True
    
    # Get the cog instance to access its config
    cog = interaction.client.get_cog("ModSlash")
    if not cog:
        return False # Should not happen

    # Retrieve the list of configured moderator role IDs for the current server
    mod_role_ids = await cog.config.guild(interaction.guild).mod_roles()
    if not mod_role_ids:
        await interaction.response.send_message("No moderator roles have been configured on this server.", ephemeral=True)
        return False

    # Get the role IDs of the user who initiated the command
    author_role_ids = {role.id for role in interaction.user.roles}
    
    # Check if the user has any of the configured moderator roles
    if not author_role_ids.intersection(mod_role_ids):
        await interaction.response.send_message("You do not have the required role to use this command.", ephemeral=True)
        return False

    return True


class ModSlash(commands.Cog):
    """
    A cog for moderation slash commands that respects role hierarchy and has configurable moderator roles.
    """

    def __init__(self, bot: Red):
        self.bot = bot
        # Initialize the Config system for persistent storage
        self.config = Config.get_conf(self, identifier=5842647, force_registration=True)
        default_guild = {
            "mod_roles": []  # A list to store the IDs of moderator roles
        }
        self.config.register_guild(**default_guild)

    # --- Configuration Commands ---
    modslashset = app_commands.Group(name="modslashset", description="Configuration for ModSlash commands")

    @modslashset.command(name="addrole", description="Adds a role that can use ModSlash commands.")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def add_mod_role(self, interaction: discord.Interaction, role: discord.Role):
        """Adds a moderator role."""
        async with self.config.guild(interaction.guild).mod_roles() as mod_roles:
            if role.id in mod_roles:
                await interaction.response.send_message(f"{role.mention} is already a moderator role.", ephemeral=True)
            else:
                mod_roles.append(role.id)
                await interaction.response.send_message(f"{role.mention} has been added as a moderator role.", ephemeral=True)

    @modslashset.command(name="removerole", description="Removes a role from the ModSlash moderators.")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def remove_mod_role(self, interaction: discord.Interaction, role: discord.Role):
        """Removes a moderator role."""
        async with self.config.guild(interaction.guild).mod_roles() as mod_roles:
            if role.id not in mod_roles:
                await interaction.response.send_message(f"{role.mention} is not a moderator role.", ephemeral=True)
            else:
                mod_roles.remove(role.id)
                await interaction.response.send_message(f"{role.mention} has been removed from the moderator roles.", ephemeral=True)

    @modslashset.command(name="listroles", description="Lists the roles that can use ModSlash commands.")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def list_mod_roles(self, interaction: discord.Interaction):
        """Lists the current moderator roles."""
        mod_role_ids = await self.config.guild(interaction.guild).mod_roles()
        if not mod_role_ids:
            await interaction.response.send_message("No moderator roles are configured.", ephemeral=True)
            return

        role_mentions = [f"<@&{role_id}>" for role_id in mod_role_ids]
        await interaction.response.send_message(f"Moderator roles: {', '.join(role_mentions)}", ephemeral=True, allowed_mentions=discord.AllowedMentions.none())


    # --- Helper Functions ---
    async def _check_voice_channel(self, interaction: discord.Interaction, member: discord.Member) -> bool:
        """Helper function to check if a member is in a voice channel."""
        if not member.voice or not member.voice.channel:
            await interaction.response.send_message(f"{member.mention} is not in a voice channel.", ephemeral=True)
            return False
        return True

    # --- Moderation Commands ---
    @app_commands.command(name="kick", description="Kicks a user from the server.")
    @app_commands.describe(member="The user to kick.", reason="The reason for the kick.")
    @app_commands.check(is_mod_check)
    async def kick_slash(self, interaction: discord.Interaction, member: discord.Member, reason: str = "No reason provided."):
        """Kicks a member from the server, checking role hierarchy."""
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
    @app_commands.describe(member="The user to ban.", reason="The reason for the ban.")
    @app_commands.check(is_mod_check)
    async def ban_slash(self, interaction: discord.Interaction, member: discord.Member, reason: str = "No reason provided."):
        """Bans a member from the server, checking role hierarchy."""
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
    @app_commands.describe(member="The user to mute.", reason="The reason for the mute.")
    @app_commands.check(is_mod_check)
    async def mute_slash(self, interaction: discord.Interaction, member: discord.Member, reason: str = "No reason provided."):
        """Mutes a member in a voice channel."""
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
    @app_commands.describe(member="The user to unmute.")
    @app_commands.check(is_mod_check)
    async def unmute_slash(self, interaction: discord.Interaction, member: discord.Member):
        """Unmutes a member in a voice channel."""
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
    @app_commands.describe(member="The user to deafen.", reason="The reason for the deafen.")
    @app_commands.check(is_mod_check)
    async def deafen_slash(self, interaction: discord.Interaction, member: discord.Member, reason: str = "No reason provided."):
        """Deafens a member in a voice channel."""
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
    @app_commands.describe(member="The user to undeafen.")
    @app_commands.check(is_mod_check)
    async def undeafen_slash(self, interaction: discord.Interaction, member: discord.Member):
        """Undeafens a member in a voice channel."""
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
    @app_commands.describe(member="The user to silence.", reason="The reason for the silence.")
    @app_commands.check(is_mod_check)
    async def silence_slash(self, interaction: discord.Interaction, member: discord.Member, reason: str = "No reason provided."):
        """Mutes and deafens a member in a voice channel."""
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
    @app_commands.describe(member="The user to unsilence.")
    @app_commands.check(is_mod_check)
    async def unsilence_slash(self, interaction: discord.Interaction, member: discord.Member):
        """Unmutes and undeafens a member in a voice channel."""
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
