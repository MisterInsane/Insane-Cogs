import discord
import sys
from pathlib import Path
from redbot.core import commands

BUILTIN_COGS = {
    "admin", "alias", "audio", "cleanup", "customcom", "downloader", 
    "economy", "filter", "general", "image", "mod", "modlog", 
    "mutes", "permissions", "reports", "streams", "trivia", "warnings"
}

def get_local_repo_name(module_name: str) -> str:
    module = sys.modules.get(module_name)
    if not module or not hasattr(module, "__file__"):
        for ext_name in sys.modules:
            if ext_name.endswith(f".{module_name}"):
                module = sys.modules[ext_name]
                break
    if module and hasattr(module, "__file__") and module.__file__:
        try:
            path = Path(module.__file__).resolve()
            for parent in path.parents:
                if (parent / ".git").is_dir():
                    return parent.name
        except Exception:
            pass
    return None

async def get_unloaded_repo_name(ctx: commands.Context, module_name: str) -> str:
    try:
        paths = await ctx.bot._cog_mgr.paths()
        for path_str in paths:
            path = Path(path_str).resolve()
            cog_dir = path / module_name
            if cog_dir.is_dir():
                for parent in cog_dir.parents:
                    if (parent / ".git").is_dir():
                        return parent.name
    except Exception:
        pass
    return None

async def get_cog_repo(ctx: commands.Context, module_name: str, downloader_map: dict) -> str:
    if module_name in downloader_map:
        return downloader_map[module_name]
        
    local_repo = get_local_repo_name(module_name)
    if local_repo:
        return local_repo
        
    unloaded_repo = await get_unloaded_repo_name(ctx, module_name)
    if unloaded_repo:
        return unloaded_repo
        
    if module_name in BUILTIN_COGS or module_name == "customcogs":
        return "Built-in"
        
    loaded_extensions = ctx.bot.extensions
    for ext in loaded_extensions:
        if ext.endswith(f".{module_name}") and ext.startswith("redbot.cogs."):
            return "Built-in"
            
    return "Local / Unknown"


class CogsView(discord.ui.View):
    def __init__(self, ctx: commands.Context, repos_data: dict, bot_color: discord.Color):
        super().__init__(timeout=120)
        self.ctx = ctx
        self.repos_data = repos_data
        self.bot_color = bot_color
        self.message = None
        self.current_page = "home"
        
        self.btn_home = discord.ui.Button(label="Summary", style=discord.ButtonStyle.primary, emoji="🏠", custom_id="cogs_btn_home")
        self.btn_home.callback = self.show_home
        
        self.btn_loaded = discord.ui.Button(label="Loaded Cogs", style=discord.ButtonStyle.success, emoji="✅", custom_id="cogs_btn_loaded")
        self.btn_loaded.callback = self.show_loaded
        
        self.btn_unloaded = discord.ui.Button(label="Unloaded Cogs", style=discord.ButtonStyle.secondary, emoji="❌", custom_id="cogs_btn_unloaded")
        self.btn_unloaded.callback = self.show_unloaded
        
        self.btn_close = discord.ui.Button(label="Close", style=discord.ButtonStyle.danger, emoji="🗑️", custom_id="cogs_btn_close")
        self.btn_close.callback = self.close_menu
        
        self.update_components()
        
    def update_components(self):
        self.clear_items()
        self.add_item(self.btn_home)
        self.add_item(self.btn_loaded)
        self.add_item(self.btn_unloaded)
        self.add_item(self.btn_close)
        
        # Highlight active button
        self.btn_home.style = discord.ButtonStyle.primary if self.current_page == "home" else discord.ButtonStyle.secondary
        self.btn_loaded.style = discord.ButtonStyle.success if self.current_page == "loaded" else discord.ButtonStyle.secondary
        self.btn_unloaded.style = discord.ButtonStyle.danger if self.current_page == "unloaded" else discord.ButtonStyle.secondary

    def get_home_embed(self) -> discord.Embed:
        embed = discord.Embed(
            title="🧩 Bot Cogs Dashboard",
            description="Welcome to your bot's cog manager dashboard! Use the buttons below to browse loaded and unloaded cogs grouped by their repositories.",
            color=self.bot_color
        )
        
        total_loaded = 0
        total_unloaded = 0
        
        for repo_name, cogs in sorted(self.repos_data.items()):
            loaded_count = len(cogs["loaded"])
            unloaded_count = len(cogs["unloaded"])
            total_loaded += loaded_count
            total_unloaded += unloaded_count
            
            repo_summary = (
                f"🟢 **Loaded:** `{loaded_count}` cogs\n"
                f"🔴 **Unloaded:** `{unloaded_count}` cogs"
            )
            embed.add_field(
                name=f"📦 {repo_name}",
                value=repo_summary,
                inline=True
            )
            
        embed.add_field(
            name="📊 System Summary",
            value=(
                f"**Total Loaded:** `{total_loaded}`\n"
                f"**Total Unloaded:** `{total_unloaded}`\n"
                f"**Total Registered:** `{total_loaded + total_unloaded}`"
            ),
            inline=False
        )
        embed.set_footer(text=f"Prefix: {self.ctx.clean_prefix} • Requested by {self.ctx.author.name}")
        return embed

    def get_loaded_embed(self) -> discord.Embed:
        embed = discord.Embed(
            title="✅ Loaded Cogs",
            description="Below are all loaded cogs, grouped by the repository/source they are installed from.",
            color=discord.Color.green()
        )
        
        has_items = False
        for repo_name, cogs in sorted(self.repos_data.items()):
            loaded_list = cogs["loaded"]
            if loaded_list:
                has_items = True
                cogs_str = ", ".join(f"`{c}`" for c in sorted(loaded_list))
                embed.add_field(
                    name=f"📦 {repo_name} ({len(loaded_list)})",
                    value=cogs_str,
                    inline=False
                )
                
        if not has_items:
            embed.description = "No cogs are currently loaded."
            
        embed.set_footer(text=f"Requested by {self.ctx.author.name}")
        return embed

    def get_unloaded_embed(self) -> discord.Embed:
        embed = discord.Embed(
            title="❌ Unloaded Cogs",
            description="Below are all unloaded cogs, grouped by the repository/source they are installed from.",
            color=discord.Color.red()
        )
        
        has_items = False
        for repo_name, cogs in sorted(self.repos_data.items()):
            unloaded_list = cogs["unloaded"]
            if unloaded_list:
                has_items = True
                cogs_str = ", ".join(f"`{c}`" for c in sorted(unloaded_list))
                embed.add_field(
                    name=f"📦 {repo_name} ({len(unloaded_list)})",
                    value=cogs_str,
                    inline=False
                )
                
        if not has_items:
            embed.description = "No cogs are currently unloaded."
            
        embed.set_footer(text=f"Requested by {self.ctx.author.name}")
        return embed

    async def show_home(self, interaction: discord.Interaction):
        self.current_page = "home"
        self.update_components()
        await interaction.response.edit_message(embed=self.get_home_embed(), view=self)

    async def show_loaded(self, interaction: discord.Interaction):
        self.current_page = "loaded"
        self.update_components()
        await interaction.response.edit_message(embed=self.get_loaded_embed(), view=self)

    async def show_unloaded(self, interaction: discord.Interaction):
        self.current_page = "unloaded"
        self.update_components()
        await interaction.response.edit_message(embed=self.get_unloaded_embed(), view=self)

    async def close_menu(self, interaction: discord.Interaction):
        await interaction.message.delete()
        self.stop()

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.ctx.author.id:
            await interaction.response.send_message(
                "This dashboard belongs to someone else. Run the cogs command yourself to browse!",
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


class CustomCogs(commands.Cog):
    """
    A sleek, interactive replacement for the standard Red cogs command.
    """
    def __init__(self, bot):
        self.bot = bot
        self.old_cogs_cmd = self.bot.remove_command("cogs")

    def cog_unload(self):
        if self.old_cogs_cmd:
            self.bot.remove_command("cogs")
            self.bot.add_command(self.old_cogs_cmd)

    @commands.command(name="cogs")
    @commands.is_owner()
    async def cogs_override(self, ctx: commands.Context):
        """
        List all loaded and unloaded cogs grouped by their repository/source.
        """
        async with ctx.typing():
            # Build downloader map if downloader is available
            downloader_map = {}
            downloader = self.bot.get_cog("Downloader")
            if downloader:
                try:
                    installed_cogs = await downloader.installed_cogs()
                    for cog in installed_cogs:
                        if cog.repo:
                            downloader_map[cog.name] = cog.repo.name
                except Exception:
                    pass

            available_modules = await self.bot._cog_mgr.available_modules()
            loaded_extensions = self.bot.extensions

            repos_data = {}

            for module_name in available_modules:
                is_loaded = any(
                    ext == module_name or ext.endswith(f".{module_name}") 
                    for ext in loaded_extensions
                )
                
                repo_name = await get_cog_repo(ctx, module_name, downloader_map)
                
                if repo_name not in repos_data:
                    repos_data[repo_name] = {"loaded": [], "unloaded": []}
                    
                if is_loaded:
                    repos_data[repo_name]["loaded"].append(module_name)
                else:
                    repos_data[repo_name]["unloaded"].append(module_name)

            bot_color = await ctx.embed_color()
            view = CogsView(ctx, repos_data, bot_color)
            embed = view.get_home_embed()
            
        msg = await ctx.send(embed=embed, view=view)
        view.message = msg
