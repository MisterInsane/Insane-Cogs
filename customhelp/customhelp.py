import discord
from discord.ext import commands as dpy_commands
from redbot.core import Config, commands
from redbot.core.commands.help import HelpFormatterABC, HelpSettings, HelpTarget
import typing
import asyncio
import difflib
import math

DEFAULT_EMOJIS = {
    "moderation": "🛡️",
    "admin": "⚙️",
    "fun": "🎮",
    "general": "📁",
    "music": "🎵",
    "economy": "💰",
    "utility": "🔧",
    "cog": "🧩",
    "customhelp": "🤖",
    "modslash": "⚔️",
    "filter": "🔇",
    "permissions": "🔑",
    "trivia": "❓",
    "reports": "📋",
    "audio": "🔊",
    "image": "🖼️",
    "owner": "👑",
    "uncategorized": "📁"
}

def get_cog_emoji(cog_name: str, custom_emojis: dict) -> str:
    if cog_name in custom_emojis:
        return custom_emojis[cog_name]
    return DEFAULT_EMOJIS.get(cog_name.lower(), "📁")


class CogSelect(discord.ui.Select):
    def __init__(self, cogs_list: typing.List[str], custom_emojis: dict, placeholder="Choose a category..."):
        options = [
            discord.SelectOption(
                label="Home",
                value="home_page",
                description="Go back to the main help menu",
                emoji="🏠"
            )
        ]
        
        for cog_name in cogs_list:
            emoji = get_cog_emoji(cog_name, custom_emojis)
            options.append(discord.SelectOption(
                label=cog_name[:100],
                value=cog_name[:100],
                emoji=emoji
            ))
            
        # Select menu options limit is 25 in Discord UI
        options = options[:25]
        
        super().__init__(
            placeholder=placeholder,
            min_values=1,
            max_values=1,
            options=options,
            custom_id="help_cog_select"
        )
        
    async def callback(self, interaction: discord.Interaction):
        view: HelpView = self.view
        selected = self.values[0]
        if selected == "home_page":
            await view.show_home(interaction)
        else:
            await view.show_cog(interaction, selected)


class CommandSelect(discord.ui.Select):
    def __init__(self, commands_list: typing.List[commands.Command], placeholder="Select a command to view details..."):
        options = []
        for cmd in commands_list:
            desc = cmd.short_doc or "No description."
            if len(desc) > 100:
                desc = desc[:97] + "..."
            options.append(discord.SelectOption(
                label=cmd.name[:100],
                value=cmd.qualified_name[:100],
                description=desc,
                emoji="🔹"
            ))
        super().__init__(
            placeholder=placeholder,
            min_values=1,
            max_values=1,
            options=options,
            custom_id="help_command_select"
        )
        
    async def callback(self, interaction: discord.Interaction):
        view: HelpView = self.view
        selected = self.values[0]
        await view.show_command_details(interaction, selected)


class SearchModal(discord.ui.Modal):
    def __init__(self, view: "HelpView"):
        super().__init__(title="Search Bot Commands", custom_id="help_search_modal")
        self.view = view
        
        self.query_input = discord.ui.TextInput(
            label="Command Name or Category",
            placeholder="e.g. ban or ModSlash",
            min_length=1,
            max_length=100,
            required=True,
            custom_id="search_query"
        )
        self.add_item(self.query_input)
        
    async def on_submit(self, interaction: discord.Interaction):
        query = self.query_input.value.strip()
        
        # Match Cog
        cogs_matched = [c for c in self.view.cogs_data.keys() if c.lower() == query.lower()]
        if cogs_matched:
            await self.view.show_cog(interaction, cogs_matched[0])
            return
            
        # Search commands
        all_visible_commands = []
        for cmds in self.view.cogs_data.values():
            all_visible_commands.extend(cmds)
            
        # Exact match
        exact_match = None
        for cmd in all_visible_commands:
            if cmd.qualified_name.lower() == query.lower():
                exact_match = cmd
                break
            if query.lower() in [a.lower() for a in cmd.aliases]:
                exact_match = cmd
                break
                
        if exact_match:
            cog_name = exact_match.cog_name or "Uncategorized"
            if cog_name in self.view.cogs_data:
                self.view.current_cog = cog_name
                self.view.current_command = exact_match
                self.view.refresh_components()
                embed = self.view.formatter.get_command_help_embed(self.view.ctx, exact_match, self.view.config_data)
                await interaction.response.edit_message(embed=embed, view=self.view)
            else:
                embed = self.view.formatter.get_command_help_embed(self.view.ctx, exact_match, self.view.config_data)
                await interaction.response.send_message(embed=embed, ephemeral=True)
            return
            
        # Fuzzy match
        names = [cmd.qualified_name for cmd in all_visible_commands]
        matches = difflib.get_close_matches(query, names, n=5, cutoff=0.3)
        
        color = self.view.config_data.get("embed_color", 0x2f3136)
        if matches:
            embed = discord.Embed(
                title="🔍 Search Results",
                description=f"No exact match for `{query}`. Here are some close matches:",
                color=color
            )
            for match in matches:
                cmd_obj = next((c for c in all_visible_commands if c.qualified_name == match), None)
                if cmd_obj:
                    short = cmd_obj.short_doc or "No description."
                    embed.add_field(
                        name=f"`{self.view.ctx.clean_prefix}{cmd_obj.qualified_name}`",
                        value=short,
                        inline=False
                    )
            await interaction.response.send_message(embed=embed, ephemeral=True)
        else:
            embed = discord.Embed(
                title="🔍 Search Results",
                description=f"No commands or categories found matching `{query}`.",
                color=discord.Color.red()
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)


class HelpView(discord.ui.View):
    def __init__(self, ctx: commands.Context, formatter: "CustomHelpFormatter", cogs_data: dict, help_settings: HelpSettings, config_data: dict):
        super().__init__(timeout=120)
        self.ctx = ctx
        self.formatter = formatter
        self.cogs_data = cogs_data
        self.help_settings = help_settings
        self.config_data = config_data
        self.message = None
        
        self.current_cog = None
        self.current_command = None
        self.current_page = 0
        self.COMMANDS_PER_PAGE = 8
        
        cogs_list = sorted(list(cogs_data.keys()))
        self.select_menu = CogSelect(cogs_list, config_data.get("cog_emojis", {}))
        
        self.btn_prev = discord.ui.Button(label="◀", style=discord.ButtonStyle.secondary, custom_id="help_btn_prev")
        self.btn_prev.callback = self.prev_page
        
        self.btn_next = discord.ui.Button(label="▶", style=discord.ButtonStyle.secondary, custom_id="help_btn_next")
        self.btn_next.callback = self.next_page
        
        self.btn_back = discord.ui.Button(label="◀ Back to List", style=discord.ButtonStyle.primary, custom_id="help_btn_back")
        self.btn_back.callback = self.back_to_cog_list
        
        self.btn_home = discord.ui.Button(label="Home", style=discord.ButtonStyle.secondary, emoji="🏠", custom_id="help_btn_home")
        self.btn_home.callback = self.go_home_button
        
        self.btn_search = discord.ui.Button(label="Search", style=discord.ButtonStyle.primary, emoji="🔍", custom_id="help_btn_search")
        self.btn_search.callback = self.open_search
        
        self.btn_delete = discord.ui.Button(label="Close", style=discord.ButtonStyle.danger, emoji="❌", custom_id="help_btn_delete")
        self.btn_delete.callback = self.delete_menu
        
        self.refresh_components()
        
    def refresh_components(self):
        self.clear_items()
        self.add_item(self.select_menu)
        
        if self.current_cog is None:
            self.add_item(self.btn_search)
            self.add_item(self.btn_delete)
        elif self.current_command is not None:
            self.add_item(self.btn_back)
            self.add_item(self.btn_search)
            self.add_item(self.btn_delete)
        else:
            commands_list = self.cogs_data[self.current_cog]
            start_idx = self.current_page * self.COMMANDS_PER_PAGE
            end_idx = start_idx + self.COMMANDS_PER_PAGE
            page_commands = commands_list[start_idx:end_idx]
            
            if page_commands:
                self.command_select = CommandSelect(page_commands)
                self.add_item(self.command_select)
                
            self.add_item(self.btn_prev)
            self.add_item(self.btn_next)
            self.add_item(self.btn_home)
            self.add_item(self.btn_search)
            self.add_item(self.btn_delete)
            
            total_pages = math.ceil(len(commands_list) / self.COMMANDS_PER_PAGE)
            self.btn_prev.disabled = (self.current_page == 0)
            self.btn_next.disabled = (self.current_page >= total_pages - 1)
            
    def get_home_embed(self) -> discord.Embed:
        color = self.config_data.get("embed_color", 0x2f3136)
        title = self.config_data.get("custom_title", "🤖 Bot Help Menu")
        desc = self.config_data.get("custom_description", "Welcome to the interactive help menu! Use the dropdown below to explore categories, or click search.")
        thumbnail = self.config_data.get("thumbnail_url", "")
        
        embed = discord.Embed(
            title=title,
            description=desc,
            color=color
        )
        if thumbnail:
            embed.set_thumbnail(url=thumbnail)
            
        total_cogs = len(self.cogs_data)
        total_commands = sum(len(cmds) for cmds in self.cogs_data.values())
        
        embed.add_field(name="Prefix", value=f"`{self.ctx.clean_prefix}`", inline=True)
        embed.add_field(name="Categories", value=f"`{total_cogs}`", inline=True)
        embed.add_field(name="Total Commands", value=f"`{total_commands}`", inline=True)
        
        cog_lines = []
        custom_emojis = self.config_data.get("cog_emojis", {})
        for name in sorted(self.cogs_data.keys()):
            emoji = get_cog_emoji(name, custom_emojis)
            count = len(self.cogs_data[name])
            cog_lines.append(f"{emoji} **{name}** ({count} commands)")
            
        if cog_lines:
            embed.add_field(
                name="Available Categories",
                value="\n".join(cog_lines),
                inline=False
            )
            
        if self.help_settings.tagline:
            embed.set_footer(text=self.help_settings.tagline)
            
        return embed
        
    def get_cog_embed(self) -> discord.Embed:
        cog_name = self.current_cog
        commands_list = self.cogs_data[cog_name]
        
        start_idx = self.current_page * self.COMMANDS_PER_PAGE
        end_idx = start_idx + self.COMMANDS_PER_PAGE
        page_commands = commands_list[start_idx:end_idx]
        
        color = self.config_data.get("embed_color", 0x2f3136)
        custom_emojis = self.config_data.get("cog_emojis", {})
        emoji = get_cog_emoji(cog_name, custom_emojis)
        
        cog_desc = "No description provided for this category."
        if cog_name == "Uncategorized":
            cog_desc = "Commands that do not belong to any specific category."
        else:
            cog_obj = self.ctx.bot.get_cog(cog_name)
            if cog_obj and cog_obj.__doc__:
                cog_desc = cog_obj.__doc__.strip()
                
        if len(cog_desc) > 500:
            cog_desc = cog_desc[:497] + "..."
            
        total_pages = math.ceil(len(commands_list) / self.COMMANDS_PER_PAGE)
        
        embed = discord.Embed(
            title=f"{emoji} {cog_name} Commands",
            description=f"*{cog_desc}*\n\nPrefix: `{self.ctx.clean_prefix}`",
            color=color
        )
        
        for cmd in page_commands:
            short_doc = cmd.short_doc or "No description provided."
            if len(short_doc) > 150:
                short_doc = short_doc[:147] + "..."
                
            signature = cmd.signature
            name_and_sig = f"{self.ctx.clean_prefix}{cmd.qualified_name} {signature}"
            
            cmd_type = ""
            if isinstance(cmd, dpy_commands.Group):
                cmd_type = " `[Group]`"
                
            embed.add_field(
                name=f"`{self.ctx.clean_prefix}{cmd.name}`{cmd_type}",
                value=f"**Usage:** `{name_and_sig.strip()}`\n{short_doc}",
                inline=False
            )
            
        embed.set_footer(text=f"Page {self.current_page + 1} of {total_pages} • Total Commands: {len(commands_list)}")
        return embed

    async def show_home(self, interaction: discord.Interaction):
        self.current_cog = None
        self.current_command = None
        self.current_page = 0
        self.refresh_components()
        embed = self.get_home_embed()
        await interaction.response.edit_message(embed=embed, view=self)
        
    async def show_cog(self, interaction: discord.Interaction, cog_name: str):
        self.current_cog = cog_name
        self.current_command = None
        self.current_page = 0
        self.refresh_components()
        embed = self.get_cog_embed()
        await interaction.response.edit_message(embed=embed, view=self)
        
    async def prev_page(self, interaction: discord.Interaction):
        if self.current_cog and self.current_page > 0:
            self.current_page -= 1
            self.refresh_components()
            embed = self.get_cog_embed()
            await interaction.response.edit_message(embed=embed, view=self)
            
    async def next_page(self, interaction: discord.Interaction):
        if self.current_cog:
            commands_list = self.cogs_data[self.current_cog]
            total_pages = math.ceil(len(commands_list) / self.COMMANDS_PER_PAGE)
            if self.current_page < total_pages - 1:
                self.current_page += 1
                self.refresh_components()
                embed = self.get_cog_embed()
                await interaction.response.edit_message(embed=embed, view=self)
                
    async def go_home_button(self, interaction: discord.Interaction):
        await self.show_home(interaction)
        
    async def back_to_cog_list(self, interaction: discord.Interaction):
        self.current_command = None
        self.refresh_components()
        embed = self.get_cog_embed()
        await interaction.response.edit_message(embed=embed, view=self)
        
    async def show_command_details(self, interaction: discord.Interaction, command_name: str):
        commands_list = self.cogs_data[self.current_cog]
        cmd_obj = next((c for c in commands_list if c.qualified_name == command_name), None)
        if cmd_obj:
            self.current_command = cmd_obj
            self.refresh_components()
            embed = self.formatter.get_command_help_embed(self.ctx, cmd_obj, self.config_data)
            await interaction.response.edit_message(embed=embed, view=self)
        else:
            await interaction.response.send_message("Command details not found.", ephemeral=True)
            
    async def open_search(self, interaction: discord.Interaction):
        await interaction.response.send_modal(SearchModal(self))
        
    async def delete_menu(self, interaction: discord.Interaction):
        await interaction.message.delete()
        self.stop()
        
    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.ctx.author.id:
            await interaction.response.send_message(
                "This help menu belongs to someone else. Run the help command yourself to browse!",
                ephemeral=True
            )
            return False
        return True
        
    async def on_timeout(self):
        if self.message:
            try:
                for item in self.children:
                    item.disabled = True
                await self.message.edit(view=self)
            except Exception:
                pass


class CustomHelpFormatter(HelpFormatterABC):
    def __init__(self, cog: "CustomHelp"):
        self.cog = cog
        
    async def get_cogs_and_commands(self, ctx: commands.Context, help_settings: HelpSettings) -> dict:
        cogs_data = {}
        for cog_name, cog in ctx.bot.cogs.items():
            cog_commands = cog.get_commands()
            filtered = []
            for cmd in cog_commands:
                if cmd.hidden and not help_settings.show_hidden:
                    continue
                if help_settings.verify_checks:
                    try:
                        can_run = await cmd.can_run(ctx)
                        if not can_run:
                            continue
                    except Exception:
                        continue
                filtered.append(cmd)
            if filtered:
                cogs_data[cog_name] = sorted(filtered, key=lambda c: c.name)
                
        uncategorized = []
        for cmd in ctx.bot.commands:
            if cmd.cog is None:
                if cmd.hidden and not help_settings.show_hidden:
                    continue
                if help_settings.verify_checks:
                    try:
                        can_run = await cmd.can_run(ctx)
                        if not can_run:
                            continue
                    except Exception:
                        continue
                uncategorized.append(cmd)
        if uncategorized:
            cogs_data["Uncategorized"] = sorted(uncategorized, key=lambda c: c.name)
            
        return cogs_data
        
    def get_command_help_embed(self, ctx: commands.Context, cmd: commands.Command, config_data: dict) -> discord.Embed:
        color = config_data.get("embed_color", 0x2f3136)
        
        subcommands_text = ""
        if isinstance(cmd, dpy_commands.Group):
            visible_subcmds = []
            for sub_cmd in cmd.commands:
                if not sub_cmd.hidden:
                    visible_subcmds.append(sub_cmd)
            if visible_subcmds:
                subcommands_list = []
                for sub_cmd in sorted(visible_subcmds, key=lambda c: c.name):
                    short = sub_cmd.short_doc or "No description."
                    subcommands_list.append(f"`{ctx.clean_prefix}{sub_cmd.qualified_name}` - {short}")
                subcommands_text = "\n".join(subcommands_list)
                
        aliases_text = "None"
        if cmd.aliases:
            aliases_text = ", ".join(f"`{a}`" for a in cmd.aliases)
            
        cooldown_text = "None"
        if cmd.cooldown:
            cooldown_text = f"{cmd.cooldown.rate} request(s) every {cmd.cooldown.per} seconds"
            
        desc = cmd.help or "No description provided."
        if len(desc) > 2000:
            desc = desc[:1997] + "..."
            
        embed = discord.Embed(
            title=f"📝 Command: {cmd.qualified_name}",
            description=desc,
            color=color
        )
        
        embed.add_field(
            name="Usage",
            value=f"`{ctx.clean_prefix}{cmd.qualified_name} {cmd.signature}`",
            inline=False
        )
        embed.add_field(name="Category", value=f"`{cmd.cog_name or 'Uncategorized'}`", inline=True)
        embed.add_field(name="Aliases", value=aliases_text, inline=True)
        embed.add_field(name="Cooldown", value=cooldown_text, inline=True)
        
        if subcommands_text:
            if len(subcommands_text) > 1024:
                subcommands_text = subcommands_text[:1020] + "\n..."
            embed.add_field(
                name="Subcommands",
                value=subcommands_text,
                inline=False
            )
            
        return embed

    async def send_help(
        self, ctx: commands.Context, help_for: HelpTarget = None, *, from_help_command: bool = False
    ):
        help_settings = await HelpSettings.from_context(ctx)
        
        config_data = {
            "embed_color": await self.cog.config.embed_color(),
            "cog_emojis": await self.cog.config.cog_emojis(),
            "thumbnail_url": await self.cog.config.thumbnail_url(),
            "custom_title": await self.cog.config.custom_title(),
            "custom_description": await self.cog.config.custom_description(),
        }
        
        # Home Page Help
        if help_for is None or isinstance(help_for, dpy_commands.bot.BotBase):
            cogs_data = await self.get_cogs_and_commands(ctx, help_settings)
            if not cogs_data:
                await ctx.send("No commands are available for you to view.")
                return
                
            view = HelpView(ctx, self, cogs_data, help_settings, config_data)
            embed = view.get_home_embed()
            msg = await ctx.send(embed=embed, view=view)
            view.message = msg
            return
            
        # String help (command name, category name, etc.)
        if isinstance(help_for, str):
            cogs_data = await self.get_cogs_and_commands(ctx, help_settings)
            cogs_matched = [c for c in cogs_data.keys() if c.lower() == help_for.lower()]
            if cogs_matched:
                cog_name = cogs_matched[0]
                view = HelpView(ctx, self, cogs_data, help_settings, config_data)
                view.current_cog = cog_name
                view.current_page = 0
                view.refresh_components()
                embed = view.get_cog_embed()
                msg = await ctx.send(embed=embed, view=view)
                view.message = msg
                return
                
            # Parse as command name
            all_visible_commands = []
            for cmds in cogs_data.values():
                all_visible_commands.extend(cmds)
                
            cmd_found = None
            for cmd in all_visible_commands:
                if cmd.qualified_name.lower() == help_for.lower():
                    cmd_found = cmd
                    break
                if help_for.lower() in [a.lower() for a in cmd.aliases]:
                    cmd_found = cmd
                    break
                    
            if cmd_found:
                # Show Command help page in channel with navigation back to its cog
                cog_name = cmd_found.cog_name or "Uncategorized"
                if cog_name in cogs_data:
                    view = HelpView(ctx, self, cogs_data, help_settings, config_data)
                    view.current_cog = cog_name
                    view.current_command = cmd_found
                    view.refresh_components()
                    embed = self.get_command_help_embed(ctx, cmd_found, config_data)
                    msg = await ctx.send(embed=embed, view=view)
                    view.message = msg
                else:
                    embed = self.get_command_help_embed(ctx, cmd_found, config_data)
                    await ctx.send(embed=embed)
                return
                
            # Fuzzy match
            names = [cmd.qualified_name for cmd in all_visible_commands]
            matches = difflib.get_close_matches(help_for, names, n=5, cutoff=0.3)
            color = config_data.get("embed_color", 0x2f3136)
            if matches:
                embed = discord.Embed(
                    title="🔍 Command Not Found",
                    description=f"No command found matching `{help_for}`. Did you mean one of these?",
                    color=color
                )
                for match in matches:
                    cmd_obj = next((c for c in all_visible_commands if c.qualified_name == match), None)
                    if cmd_obj:
                        short = cmd_obj.short_doc or "No description."
                        embed.add_field(
                            name=f"`{ctx.clean_prefix}{cmd_obj.qualified_name}`",
                            value=short,
                            inline=False
                        )
                await ctx.send(embed=embed)
            else:
                await ctx.send(f"No command or category found matching `{help_for}`.")
            return
            
        # Cog object help
        if isinstance(help_for, commands.Cog):
            cogs_data = await self.get_cogs_and_commands(ctx, help_settings)
            cog_name = help_for.qualified_name
            if cog_name in cogs_data:
                view = HelpView(ctx, self, cogs_data, help_settings, config_data)
                view.current_cog = cog_name
                view.current_page = 0
                view.refresh_components()
                embed = view.get_cog_embed()
                msg = await ctx.send(embed=embed, view=view)
                view.message = msg
            else:
                await ctx.send(f"No commands are available in category `{cog_name}`.")
            return
            
        # Command/Group object help
        if isinstance(help_for, (commands.Command, dpy_commands.Command)):
            if help_settings.verify_checks:
                try:
                    can_run = await help_for.can_run(ctx)
                    if not can_run:
                        await ctx.send(f"No command found matching `{help_for.qualified_name}`.")
                        return
                except Exception:
                    await ctx.send(f"No command found matching `{help_for.qualified_name}`.")
                    return
                    
            cogs_data = await self.get_cogs_and_commands(ctx, help_settings)
            cog_name = help_for.cog_name or "Uncategorized"
            if cog_name in cogs_data:
                view = HelpView(ctx, self, cogs_data, help_settings, config_data)
                view.current_cog = cog_name
                view.current_command = help_for
                view.refresh_components()
                embed = self.get_command_help_embed(ctx, help_for, config_data)
                msg = await ctx.send(embed=embed, view=view)
                view.message = msg
            else:
                embed = self.get_command_help_embed(ctx, help_for, config_data)
                await ctx.send(embed=embed)
            return


class CustomHelp(commands.Cog):
    """
    A sleek, interactive help menu cog that overrides the default Red help command.
    """
    def __init__(self, bot):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=9284729384, force_registration=True)
        
        default_global = {
            "embed_color": 0x2f3136,
            "cog_emojis": {},
            "thumbnail_url": "",
            "custom_title": "🤖 Bot Help Menu",
            "custom_description": "Welcome to the interactive help menu! Use the dropdown below to explore categories, or click search."
        }
        self.config.register_global(**default_global)
        
        self.formatter = CustomHelpFormatter(self)
        
    def cog_load(self):
        self.bot.set_help_formatter(self.formatter)
        
    def cog_unload(self):
        self.bot.reset_help_formatter()
        
    @commands.group(name="customhelpset")
    @commands.is_owner()
    async def customhelpset(self, ctx: commands.Context):
        """
        Configure the custom interactive help menu.
        """
        pass
        
    @customhelpset.command(name="color")
    async def customhelpset_color(self, ctx: commands.Context, color: discord.Color):
        """
        Set the embed color. Accepts hex codes (e.g. #7289da) or color names.
        """
        await self.config.embed_color.set(color.value)
        await ctx.send(f"Help menu embed color updated to `{color}`.")
        
    @customhelpset.command(name="emoji")
    async def customhelpset_emoji(self, ctx: commands.Context, cog_name: str, emoji: str):
        """
        Set the emoji for a specific Cog.
        """
        cogs = list(self.bot.cogs.keys()) + ["Uncategorized"]
        matched = [c for c in cogs if c.lower() == cog_name.lower()]
        if not matched:
            await ctx.send(f"Warning: `{cog_name}` is not currently a loaded Cog, but I will save this emoji association anyway.")
            cog_key = cog_name
        else:
            cog_key = matched[0]
            
        async with self.config.cog_emojis() as emojis:
            emojis[cog_key] = emoji
            
        await ctx.send(f"Help menu emoji for `{cog_key}` set to {emoji}.")
        
    @customhelpset.command(name="removeemoji")
    async def customhelpset_removeemoji(self, ctx: commands.Context, cog_name: str):
        """
        Remove the emoji association for a Cog.
        """
        async with self.config.cog_emojis() as emojis:
            if cog_name in emojis:
                del emojis[cog_name]
                await ctx.send(f"Emoji association for `{cog_name}` removed.")
            else:
                matched = None
                for k in emojis.keys():
                    if k.lower() == cog_name.lower():
                        matched = k
                        break
                if matched:
                    del emojis[matched]
                    await ctx.send(f"Emoji association for `{matched}` removed.")
                else:
                    await ctx.send(f"No custom emoji found for `{cog_name}`.")
                    
    @customhelpset.command(name="title")
    async def customhelpset_title(self, ctx: commands.Context, *, title: str):
        """
        Set the title for the help menu landing page.
        """
        if len(title) > 256:
            await ctx.send("Title must be under 256 characters.")
            return
        await self.config.custom_title.set(title)
        await ctx.send(f"Landing page title updated to: `{title}`")
        
    @customhelpset.command(name="desc")
    async def customhelpset_desc(self, ctx: commands.Context, *, description: str):
        """
        Set the description for the help menu landing page.
        """
        if len(description) > 2000:
            await ctx.send("Description must be under 2000 characters.")
            return
        await self.config.custom_description.set(description)
        await ctx.send("Landing page description updated.")
        
    @customhelpset.command(name="thumbnail")
    async def customhelpset_thumbnail(self, ctx: commands.Context, url: str):
        """
        Set the thumbnail URL for the help menu landing page. Set to 'none' to disable.
        """
        if url.lower() == "none":
            await self.config.thumbnail_url.set("")
            await ctx.send("Landing page thumbnail disabled.")
            return
            
        if not (url.startswith("http://") or url.startswith("https://")):
            await ctx.send("Please enter a valid URL starting with http:// or https://")
            return
            
        await self.config.thumbnail_url.set(url)
        await ctx.send("Landing page thumbnail updated.")
        
    @customhelpset.command(name="settings")
    async def customhelpset_settings(self, ctx: commands.Context):
        """
        View current configuration settings.
        """
        color_val = await self.config.embed_color()
        title = await self.config.custom_title()
        desc = await self.config.custom_description()
        thumb = await self.config.thumbnail_url()
        emojis = await self.config.cog_emojis()
        
        embed = discord.Embed(
            title="Interactive Help Settings",
            color=color_val
        )
        embed.add_field(name="Embed Color", value=f"`hex({hex(color_val)})`", inline=True)
        embed.add_field(name="Thumbnail URL", value=thumb or "None", inline=True)
        embed.add_field(name="Landing Title", value=title, inline=False)
        embed.add_field(name="Landing Description", value=desc, inline=False)
        
        emoji_lines = [f"{k}: {v}" for k, v in emojis.items()]
        embed.add_field(
            name="Custom Emojis",
            value="\n".join(emoji_lines) if emoji_lines else "None configured.",
            inline=False
        )
        await ctx.send(embed=embed)
        
    @customhelpset.command(name="reset")
    async def customhelpset_reset(self, ctx: commands.Context):
        """
        Reset all help settings to defaults.
        """
        await self.config.clear_all()
        await ctx.send("Custom help settings reset to default values.")
