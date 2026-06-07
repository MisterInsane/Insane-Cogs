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

def get_cog_emoji(cog_name: str, custom_emojis: dict, custom_categories: dict) -> str:
    if cog_name in custom_categories:
        emoji = custom_categories[cog_name].get("emoji")
        if emoji:
            return emoji
    if cog_name in custom_emojis:
        return custom_emojis[cog_name]
    return DEFAULT_EMOJIS.get(cog_name.lower(), "📁")


class CogSelect(discord.ui.Select):
    def __init__(self, cogs_list: typing.List[str], custom_emojis: dict, custom_categories: dict, placeholder="Choose a category..."):
        options = [
            discord.SelectOption(
                label="Home",
                value="home_page",
                description="Go back to the main help menu",
                emoji="🏠"
            )
        ]
        
        for cog_name in cogs_list:
            emoji = get_cog_emoji(cog_name, custom_emojis, custom_categories)
            options.append(discord.SelectOption(
                label=cog_name[:100],
                value=cog_name[:100],
                emoji=emoji
            ))
            
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
            # If we are using custom categories, cog_name might map differently.
            # Let's find which category this command is assigned to in view.cogs_data
            assigned_category = "Uncategorized"
            for cat_name, cmds in self.view.cogs_data.items():
                if exact_match in cmds:
                    assigned_category = cat_name
                    break
            
            self.view.current_cog = assigned_category
            self.view.current_command = exact_match
            self.view.refresh_components()
            embed = self.view.formatter.get_command_help_embed(self.view.ctx, exact_match, self.view.config_data)
            await interaction.response.edit_message(embed=embed, view=self.view)
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
        self.select_menu = CogSelect(cogs_list, config_data.get("cog_emojis", {}), config_data.get("custom_categories", {}))
        
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
        custom_categories = self.config_data.get("custom_categories", {})
        for name in sorted(self.cogs_data.keys()):
            emoji = get_cog_emoji(name, custom_emojis, custom_categories)
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
        custom_categories = self.config_data.get("custom_categories", {})
        emoji = get_cog_emoji(cog_name, custom_emojis, custom_categories)
        
        # Resolve description for category
        cog_desc = "No description provided for this category."
        if cog_name == "Uncategorized":
            cog_desc = "Unassigned commands that do not belong to any custom category."
        elif cog_name in custom_categories:
            cog_desc = f"Custom category containing commands for: {', '.join(custom_categories[cog_name].get('cogs', [])) or 'assigned commands'}"
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


# Interactive Category Management GUI Components
class CogToggleSelect(discord.ui.Select):
    def __init__(self, cogs_list: typing.List[str], assigned_cogs: list, placeholder="Toggle a Cog's assignment..."):
        options = []
        for cog_name in cogs_list:
            is_assigned = cog_name in assigned_cogs
            emoji = "✅" if is_assigned else "❌"
            options.append(discord.SelectOption(
                label=cog_name,
                value=cog_name,
                description="Assigned" if is_assigned else "Not assigned",
                emoji=emoji
            ))
        options = options[:25]
        super().__init__(
            placeholder=placeholder,
            min_values=1,
            max_values=1,
            options=options,
            custom_id="manage_cog_toggle_select"
        )
        
    async def callback(self, interaction: discord.Interaction):
        view: ManageView = self.view
        selected_cog = self.values[0]
        category = view.current_category
        
        async with view.cog.config.custom_categories() as cats:
            if category in cats:
                cogs = cats[category].get("cogs", [])
                if selected_cog in cogs:
                    cogs.remove(selected_cog)
                    action = f"Removed Cog `{selected_cog}` from category `{category}`."
                else:
                    # Remove from other categories to avoid duplicates
                    for other_cat, data in cats.items():
                        if selected_cog in data.get("cogs", []):
                            data["cogs"].remove(selected_cog)
                    cogs.append(selected_cog)
                    action = f"Added Cog `{selected_cog}` to category `{category}`."
                cats[category]["cogs"] = cogs
                
        view.cog_toggle_active = False
        await view.update_view(interaction, content=action)


class CommandRemoveSelect(discord.ui.Select):
    def __init__(self, commands_list: list, placeholder="Select a command to remove..."):
        options = []
        for cmd_name in commands_list:
            options.append(discord.SelectOption(
                label=cmd_name,
                value=cmd_name,
                emoji="🗑️"
            ))
        options = options[:25]
        super().__init__(
            placeholder=placeholder,
            min_values=1,
            max_values=1,
            options=options,
            custom_id="manage_cmd_remove_select"
        )
        
    async def callback(self, interaction: discord.Interaction):
        view: ManageView = self.view
        selected_cmd = self.values[0]
        category = view.current_category
        
        async with view.cog.config.custom_categories() as cats:
            if category in cats:
                cmds = cats[category].get("commands", [])
                if selected_cmd in cmds:
                    cmds.remove(selected_cmd)
                    action = f"Removed command `{selected_cmd}` from category `{category}`."
                cats[category]["commands"] = cmds
                
        view.cmd_remove_active = False
        await view.update_view(interaction, content=action)


class DeleteCategorySelect(discord.ui.Select):
    def __init__(self, categories_list: list, placeholder="Select a category to delete..."):
        options = []
        for cat_name in categories_list:
            options.append(discord.SelectOption(
                label=cat_name,
                value=cat_name,
                emoji="🗑️"
            ))
        options = options[:25]
        super().__init__(
            placeholder=placeholder,
            min_values=1,
            max_values=1,
            options=options,
            custom_id="manage_delete_cat_select"
        )
        
    async def callback(self, interaction: discord.Interaction):
        view: ManageView = self.view
        selected_cat = self.values[0]
        
        async with view.cog.config.custom_categories() as cats:
            if selected_cat in cats:
                del cats[selected_cat]
                
        view.delete_active = False
        await view.update_view(interaction, content=f"Deleted custom category `{selected_cat}`.")


class AddCommandModal(discord.ui.Modal):
    def __init__(self, view: "ManageView"):
        super().__init__(title="Add Command to Category", custom_id="manage_add_cmd_modal")
        self.view = view
        self.cmd_input = discord.ui.TextInput(
            label="Command Name",
            placeholder="e.g. ban",
            required=True,
            custom_id="add_cmd_input"
        )
        self.add_item(self.cmd_input)
        
    async def on_submit(self, interaction: discord.Interaction):
        cmd_name = self.cmd_input.value.strip()
        cmd_obj = self.view.cog.bot.get_command(cmd_name)
        if not cmd_obj:
            action = f"Warning: Command `{cmd_name}` was not found in the bot, but added to category anyway."
        else:
            action = f"Added command `{cmd_obj.qualified_name}` to category `{self.view.current_category}`."
            cmd_name = cmd_obj.qualified_name
            
        async with self.view.cog.config.custom_categories() as cats:
            if self.view.current_category in cats:
                cmds = cats[self.view.current_category].get("commands", [])
                
                # Remove from other categories
                for other_cat, data in cats.items():
                    if cmd_name in data.get("commands", []):
                        data["commands"].remove(cmd_name)
                        
                if cmd_name not in cmds:
                    cmds.append(cmd_name)
                cats[self.view.current_category]["commands"] = cmds
                
        await self.view.update_view(interaction, content=action)


class SetEmojiModal(discord.ui.Modal):
    def __init__(self, view: "ManageView"):
        super().__init__(title="Set Category Emoji", custom_id="manage_set_emoji_modal")
        self.view = view
        self.emoji_input = discord.ui.TextInput(
            label="Emoji",
            placeholder="e.g. 🛡️",
            required=True,
            custom_id="set_emoji_input"
        )
        self.add_item(self.emoji_input)
        
    async def on_submit(self, interaction: discord.Interaction):
        emoji = self.emoji_input.value.strip()
        async with self.view.cog.config.custom_categories() as cats:
            if self.view.current_category in cats:
                cats[self.view.current_category]["emoji"] = emoji
                
        await self.view.update_view(interaction, content=f"Category `{self.view.current_category}` emoji set to {emoji}.")


class CreateCategoryModal(discord.ui.Modal):
    def __init__(self, view: "ManageView"):
        super().__init__(title="Create Custom Category", custom_id="manage_create_cat_modal")
        self.view = view
        self.name_input = discord.ui.TextInput(
            label="Category Name",
            placeholder="e.g. Moderation",
            required=True,
            custom_id="create_cat_name"
        )
        self.emoji_input = discord.ui.TextInput(
            label="Optional Emoji",
            placeholder="e.g. 🛡️",
            required=False,
            custom_id="create_cat_emoji"
        )
        self.add_item(self.name_input)
        self.add_item(self.emoji_input)
        
    async def on_submit(self, interaction: discord.Interaction):
        name = self.name_input.value.strip()
        emoji = self.emoji_input.value.strip() or "📁"
        
        async with self.view.cog.config.custom_categories() as cats:
            if name in cats:
                await interaction.response.send_message(f"Category `{name}` already exists.", ephemeral=True)
                return
            cats[name] = {"emoji": emoji, "cogs": [], "commands": []}
            
        await self.view.update_view(interaction, content=f"Created custom category `{name}` with emoji {emoji}.")


class RenameCategoryModal(discord.ui.Modal):
    def __init__(self, view: "ManageView"):
        super().__init__(title="Rename Category", custom_id="manage_rename_cat_modal")
        self.view = view
        self.name_input = discord.ui.TextInput(
            label="New Name",
            placeholder=f"Current: {view.current_category}",
            required=True,
            custom_id="rename_cat_name"
        )
        self.add_item(self.name_input)
        
    async def on_submit(self, interaction: discord.Interaction):
        new_name = self.name_input.value.strip()
        old_name = self.view.current_category
        
        async with self.view.cog.config.custom_categories() as cats:
            if new_name in cats:
                await interaction.response.send_message(f"Category `{new_name}` already exists.", ephemeral=True)
                return
            if old_name in cats:
                cats[new_name] = cats.pop(old_name)
                
        self.view.current_category = new_name
        await self.view.update_view(interaction, content=f"Renamed category `{old_name}` to `{new_name}`.")


class ManageView(discord.ui.View):
    def __init__(self, ctx: commands.Context, cog: commands.Cog):
        super().__init__(timeout=180)
        self.ctx = ctx
        self.cog = cog
        self.message = None
        
        self.current_category = None
        self.cog_toggle_active = False
        self.cmd_remove_active = False
        self.delete_active = False
        
        self.btn_create = discord.ui.Button(label="Create Category", style=discord.ButtonStyle.success, emoji="➕", custom_id="manage_btn_create")
        self.btn_create.callback = self.create_category
        
        self.btn_delete_mode = discord.ui.Button(label="Delete Category", style=discord.ButtonStyle.danger, emoji="🗑️", custom_id="manage_btn_delete_mode")
        self.btn_delete_mode.callback = self.toggle_delete_mode
        
        self.btn_close = discord.ui.Button(label="Close", style=discord.ButtonStyle.danger, emoji="❌", custom_id="manage_btn_close")
        self.btn_close.callback = self.delete_menu
        
        self.btn_toggle_cog = discord.ui.Button(label="Assign Cogs", style=discord.ButtonStyle.primary, emoji="🧩", custom_id="manage_btn_toggle_cog")
        self.btn_toggle_cog.callback = self.toggle_cog_mode
        
        self.btn_add_cmd = discord.ui.Button(label="Add Command", style=discord.ButtonStyle.primary, emoji="➕", custom_id="manage_btn_add_cmd")
        self.btn_add_cmd.callback = self.add_command
        
        self.btn_remove_cmd = discord.ui.Button(label="Remove Command", style=discord.ButtonStyle.primary, emoji="➖", custom_id="manage_btn_remove_cmd")
        self.btn_remove_cmd.callback = self.toggle_remove_cmd_mode
        
        self.btn_set_emoji = discord.ui.Button(label="Set Emoji", style=discord.ButtonStyle.primary, emoji="🏷️", custom_id="manage_btn_set_emoji")
        self.btn_set_emoji.callback = self.set_emoji
        
        self.btn_rename = discord.ui.Button(label="Rename", style=discord.ButtonStyle.primary, emoji="✏️", custom_id="manage_btn_rename")
        self.btn_rename.callback = self.rename_category
        
        self.btn_back = discord.ui.Button(label="◀ Back", style=discord.ButtonStyle.secondary, custom_id="manage_btn_back")
        self.btn_back.callback = self.back_to_main
        
    def refresh_components_sync(self, categories: dict):
        self.clear_items()
        
        if self.current_category is None:
            if categories:
                # Local inline subclass to prevent ID conflicts
                class CategorySelect(discord.ui.Select):
                    def __init__(self, cats_list):
                        options = [
                            discord.SelectOption(label=cat_name, value=cat_name, emoji=cats_list[cat_name].get("emoji", "📁"))
                            for cat_name in cats_list.keys()
                        ]
                        super().__init__(placeholder="Select a category to edit...", options=options[:25], custom_id="manage_cat_select")
                    async def callback(self, interaction: discord.Interaction):
                        self.view.current_category = self.values[0]
                        self.view.cog_toggle_active = False
                        self.view.cmd_remove_active = False
                        await self.view.update_view(interaction)
                        
                self.add_item(CategorySelect(categories))
                
            if self.delete_active and categories:
                self.add_item(DeleteCategorySelect(list(categories.keys())))
                
            self.add_item(self.btn_create)
            if categories:
                self.add_item(self.btn_delete_mode)
            self.add_item(self.btn_close)
            
        else:
            cat_data = categories.get(self.current_category, {})
            
            if self.cog_toggle_active:
                all_cogs = sorted(list(self.cog.bot.cogs.keys()))
                self.add_item(CogToggleSelect(all_cogs, cat_data.get("cogs", [])))
                
            if self.cmd_remove_active:
                assigned_cmds = cat_data.get("commands", [])
                if assigned_cmds:
                    self.add_item(CommandRemoveSelect(assigned_cmds))
                    
            self.add_item(self.btn_toggle_cog)
            self.add_item(self.btn_add_cmd)
            if cat_data.get("commands", []):
                self.add_item(self.btn_remove_cmd)
            self.add_item(self.btn_set_emoji)
            self.add_item(self.btn_rename)
            self.add_item(self.btn_back)
            self.add_item(self.btn_close)
            
    async def update_view(self, interaction: discord.Interaction, content: str = None):
        categories = await self.cog.config.custom_categories()
        self.refresh_components_sync(categories)
        if self.current_category is None:
            embed = await self.get_main_embed(categories)
        else:
            embed = await self.get_edit_embed(categories)
        await interaction.response.edit_message(content=content, embed=embed, view=self)
        
    async def get_main_embed(self, categories: dict = None) -> discord.Embed:
        if categories is None:
            categories = await self.cog.config.custom_categories()
            
        color = await self.cog.config.embed_color()
        embed = discord.Embed(
            title="📁 Custom Help Category Manager",
            description="Use this dashboard to manage your custom help categories. "
                        "You can group cogs and individual commands into custom folders.",
            color=color
        )
        
        if not categories:
            embed.add_field(
                name="No Custom Categories",
                value="The help menu is currently using the default cog-based layout. "
                      "Click **Create Category** below to get started!",
                inline=False
            )
        else:
            for name, data in categories.items():
                emoji = data.get("emoji", "📁")
                cogs = ", ".join(f"`{c}`" for c in data.get("cogs", [])) or "*None*"
                cmds = ", ".join(f"`{c}`" for c in data.get("commands", [])) or "*None*"
                embed.add_field(
                    name=f"{emoji} {name}",
                    value=f"**Cogs:** {cogs}\n**Commands:** {cmds}",
                    inline=False
                )
        return embed

    async def get_edit_embed(self, categories: dict = None) -> discord.Embed:
        if categories is None:
            categories = await self.cog.config.custom_categories()
            
        cat_data = categories.get(self.current_category, {})
        color = await self.cog.config.embed_color()
        emoji = cat_data.get("emoji", "📁")
        
        embed = discord.Embed(
            title=f"Editing Category: {emoji} {self.current_category}",
            description="Configure cogs and commands assigned to this category. "
                        "Commands not listed here will fall back to their cog's category "
                        "unless their cog is assigned elsewhere.",
            color=color
        )
        
        cogs = ", ".join(f"`{c}`" for c in cat_data.get("cogs", [])) or "*None*"
        cmds = ", ".join(f"`{c}`" for c in cat_data.get("commands", [])) or "*None*"
        
        embed.add_field(name="Assigned Cogs", value=cogs, inline=False)
        embed.add_field(name="Assigned Commands", value=cmds, inline=False)
        
        return embed

    async def create_category(self, interaction: discord.Interaction):
        await interaction.response.send_modal(CreateCategoryModal(self))
        
    async def toggle_delete_mode(self, interaction: discord.Interaction):
        self.delete_active = not self.delete_active
        await self.update_view(interaction)
        
    async def toggle_cog_mode(self, interaction: discord.Interaction):
        self.cog_toggle_active = not self.cog_toggle_active
        self.cmd_remove_active = False
        await self.update_view(interaction)
        
    async def toggle_remove_cmd_mode(self, interaction: discord.Interaction):
        self.cmd_remove_active = not self.cmd_remove_active
        self.cog_toggle_active = False
        await self.update_view(interaction)
        
    async def add_command(self, interaction: discord.Interaction):
        await interaction.response.send_modal(AddCommandModal(self))
        
    async def set_emoji(self, interaction: discord.Interaction):
        await interaction.response.send_modal(SetEmojiModal(self))
        
    async def rename_category(self, interaction: discord.Interaction):
        await interaction.response.send_modal(RenameCategoryModal(self))
        
    async def back_to_main(self, interaction: discord.Interaction):
        self.current_category = None
        self.cog_toggle_active = False
        self.cmd_remove_active = False
        self.delete_active = False
        await self.update_view(interaction)
        
    async def delete_menu(self, interaction: discord.Interaction):
        await interaction.message.delete()
        self.stop()
        
    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.ctx.author.id:
            await interaction.response.send_message("Only the manager can control this dashboard.", ephemeral=True)
            return False
        return True


class CustomHelpFormatter(HelpFormatterABC):
    def __init__(self, cog: "CustomHelp"):
        self.cog = cog
        
    async def get_cogs_and_commands(self, ctx: commands.Context, help_settings: HelpSettings) -> dict:
        all_visible_commands = []
        for cog_name, cog in ctx.bot.cogs.items():
            cog_commands = cog.get_commands()
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
                all_visible_commands.append(cmd)
                
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
                all_visible_commands.append(cmd)
                
        custom_categories = await self.cog.config.custom_categories()
        
        # Default cog-based grouping if no custom categories
        if not custom_categories:
            cogs_data = {}
            for cmd in all_visible_commands:
                cog_name = cmd.cog_name or "Uncategorized"
                if cog_name not in cogs_data:
                    cogs_data[cog_name] = []
                cogs_data[cog_name].append(cmd)
            for k in cogs_data:
                cogs_data[k] = sorted(cogs_data[k], key=lambda c: c.name)
            return cogs_data
            
        # Custom category grouping
        cogs_data = {cat_name: [] for cat_name in custom_categories.keys()}
        cogs_data["Uncategorized"] = []
        
        for cmd in all_visible_commands:
            assigned = False
            # Check explicit command name assignment
            for cat_name, cat_data in custom_categories.items():
                if cmd.qualified_name in cat_data.get("commands", []):
                    cogs_data[cat_name].append(cmd)
                    assigned = True
                    break
            if assigned:
                continue
                
            # Check cog assignment
            if cmd.cog_name:
                for cat_name, cat_data in custom_categories.items():
                    if cmd.cog_name in cat_data.get("cogs", []):
                        cogs_data[cat_name].append(cmd)
                        assigned = True
                        break
            if assigned:
                continue
                
            cogs_data["Uncategorized"].append(cmd)
            
        # Filter empty categories
        filtered_cogs_data = {}
        for cat_name, cmds in cogs_data.items():
            if cmds:
                filtered_cogs_data[cat_name] = sorted(cmds, key=lambda c: c.name)
                
        return filtered_cogs_data
        
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
            "custom_categories": await self.cog.config.custom_categories(),
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
                assigned_category = "Uncategorized"
                for cat_name, cmds in cogs_data.items():
                    if cmd_found in cmds:
                        assigned_category = cat_name
                        break
                        
                view = HelpView(ctx, self, cogs_data, help_settings, config_data)
                view.current_cog = assigned_category
                view.current_command = cmd_found
                view.refresh_components()
                embed = self.get_command_help_embed(ctx, cmd_found, config_data)
                msg = await ctx.send(embed=embed, view=view)
                view.message = msg
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
            
            # Find which custom category includes this Cog
            assigned_category = None
            custom_categories = config_data.get("custom_categories", {})
            for cat_name, cat_data in custom_categories.items():
                if cog_name in cat_data.get("cogs", []):
                    assigned_category = cat_name
                    break
            
            # If not assigned to a custom category, check if it's visible in default categories (fallback to cog_name)
            if assigned_category is None:
                assigned_category = cog_name if cog_name in cogs_data else "Uncategorized"
                
            if assigned_category in cogs_data:
                view = HelpView(ctx, self, cogs_data, help_settings, config_data)
                view.current_cog = assigned_category
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
            assigned_category = "Uncategorized"
            for cat_name, cmds in cogs_data.items():
                if help_for in cmds:
                    assigned_category = cat_name
                    break
                    
            view = HelpView(ctx, self, cogs_data, help_settings, config_data)
            view.current_cog = assigned_category
            view.current_command = help_for
            view.refresh_components()
            embed = self.get_command_help_embed(ctx, help_for, config_data)
            msg = await ctx.send(embed=embed, view=view)
            view.message = msg
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
            "custom_description": "Welcome to the interactive help menu! Use the dropdown below to explore categories, or click search.",
            "custom_categories": {},
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
        
    @customhelpset.command(name="manage")
    async def customhelpset_manage(self, ctx: commands.Context):
        """
        Interactively manage custom help categories, cogs, and commands.
        """
        view = ManageView(ctx, self)
        categories = await self.config.custom_categories()
        view.refresh_components_sync(categories)
        embed = await view.get_main_embed(categories)
        msg = await ctx.send(embed=embed, view=view)
        view.message = msg

    @customhelpset.command(name="addcat")
    async def customhelpset_addcat(self, ctx: commands.Context, name: str, emoji: str = "📁"):
        """
        Create a new custom category.
        """
        async with self.config.custom_categories() as cats:
            if name in cats:
                await ctx.send(f"Category `{name}` already exists.")
                return
            cats[name] = {"emoji": emoji, "cogs": [], "commands": []}
        await ctx.send(f"Custom category `{name}` created with emoji {emoji}.")

    @customhelpset.command(name="delcat")
    async def customhelpset_delcat(self, ctx: commands.Context, name: str):
        """
        Delete a custom category.
        """
        async with self.config.custom_categories() as cats:
            if name in cats:
                del cats[name]
                await ctx.send(f"Custom category `{name}` deleted.")
            else:
                await ctx.send(f"Category `{name}` not found.")

    @customhelpset.command(name="assigncog")
    async def customhelpset_assigncog(self, ctx: commands.Context, category: str, cog_name: str):
        """
        Assign a Cog to a custom category.
        """
        cogs = list(self.bot.cogs.keys())
        matched = [c for c in cogs if c.lower() == cog_name.lower()]
        if not matched:
            await ctx.send(f"Warning: `{cog_name}` is not currently a loaded Cog, but I will assign it anyway.")
            cog_key = cog_name
        else:
            cog_key = matched[0]
            
        async with self.config.custom_categories() as cats:
            if category not in cats:
                await ctx.send(f"Category `{category}` does not exist. Create it first using `customhelpset addcat`.")
                return
            
            for cat, data in cats.items():
                if cog_key in data.get("cogs", []):
                    data["cogs"].remove(cog_key)
                    
            cats[category]["cogs"].append(cog_key)
            
        await ctx.send(f"Assigned Cog `{cog_key}` to category `{category}`.")

    @customhelpset.command(name="unassigncog")
    async def customhelpset_unassigncog(self, ctx: commands.Context, cog_name: str):
        """
        Remove a Cog's custom category assignment.
        """
        unassigned = False
        async with self.config.custom_categories() as cats:
            for cat, data in cats.items():
                cogs = data.get("cogs", [])
                matched = [c for c in cogs if c.lower() == cog_name.lower()]
                if matched:
                    for m in matched:
                        cogs.remove(m)
                    data["cogs"] = cogs
                    unassigned = True
                    await ctx.send(f"Removed Cog `{matched[0]}` from category `{cat}`.")
                    break
        if not unassigned:
            await ctx.send(f"Cog `{cog_name}` was not assigned to any custom category.")

    @customhelpset.command(name="assigncmd")
    async def customhelpset_assigncmd(self, ctx: commands.Context, category: str, command_name: str):
        """
        Assign an individual command to a custom category.
        """
        cmd_obj = self.bot.get_command(command_name)
        if not cmd_obj:
            await ctx.send(f"Warning: Command `{command_name}` was not found, but I will assign it anyway.")
            cmd_key = command_name
        else:
            cmd_key = cmd_obj.qualified_name
            
        async with self.config.custom_categories() as cats:
            if category not in cats:
                await ctx.send(f"Category `{category}` does not exist. Create it first using `customhelpset addcat`.")
                return
            
            for cat, data in cats.items():
                if cmd_key in data.get("commands", []):
                    data["commands"].remove(cmd_key)
                    
            cats[category]["commands"].append(cmd_key)
            
        await ctx.send(f"Assigned command `{cmd_key}` to category `{category}`.")

    @customhelpset.command(name="unassigncmd")
    async def customhelpset_unassigncmd(self, ctx: commands.Context, command_name: str):
        """
        Remove a command's custom category assignment.
        """
        unassigned = False
        async with self.config.custom_categories() as cats:
            for cat, data in cats.items():
                cmds = data.get("commands", [])
                matched = [c for c in cmds if c.lower() == command_name.lower()]
                if matched:
                    for m in matched:
                        cmds.remove(m)
                    data["commands"] = cmds
                    unassigned = True
                    await ctx.send(f"Removed command `{matched[0]}` from category `{cat}`.")
                    break
        if not unassigned:
            await ctx.send(f"Command `{command_name}` was not assigned to any custom category.")
        
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
        categories = await self.config.custom_categories()
        
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
        
        cat_lines = []
        for cat_name, cat_data in categories.items():
            cogs_count = len(cat_data.get("cogs", []))
            cmds_count = len(cat_data.get("commands", []))
            emoji = cat_data.get("emoji", "📁")
            cat_lines.append(f"{emoji} **{cat_name}** ({cogs_count} cogs, {cmds_count} commands)")
            
        embed.add_field(
            name="Custom Categories",
            value="\n".join(cat_lines) if cat_lines else "None (defaulting to cogs).",
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
        
    @customhelpset.command(name="debug")
    async def customhelpset_debug(self, ctx: commands.Context):
        """
        Debug custom categories grouping.
        """
        cats = await self.config.custom_categories()
        await ctx.send(f"Custom Categories in Config: `{cats}`")
        
        # Test get_cogs_and_commands
        help_settings = await HelpSettings.from_context(ctx)
        cogs_data = await self.formatter.get_cogs_and_commands(ctx, help_settings)
        
        cogs_summary = {}
        for cat_name, cmds in cogs_data.items():
            cogs_summary[cat_name] = [c.qualified_name for c in cmds]
            
        await ctx.send(f"Grouped cogs_data: `{cogs_summary}`")

