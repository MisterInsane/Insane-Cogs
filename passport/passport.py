import discord
import io
import datetime
import calendar
import asyncio
import aiohttp
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont
from redbot.core import commands, Config

def get_residency_duration(joined_at: datetime.datetime) -> str:
    """
    Calculates the tenure duration in a clean, human-readable format.
    E.g., "3 Years, 5 Months" or "12 Days".
    """
    if not joined_at:
        return "N/A"
    
    now = datetime.datetime.now(datetime.timezone.utc)
    diff = now - joined_at
    
    # Calculate years, months, and days accurately
    years = now.year - joined_at.year
    months = now.month - joined_at.month
    days = now.day - joined_at.day
    
    if days < 0:
        # Borrow days from the previous month
        prev_month = now.month - 1 if now.month > 1 else 12
        prev_year = now.year if now.month > 1 else now.year - 1
        _, days_in_prev = calendar.monthrange(prev_year, prev_month)
        days += days_in_prev
        months -= 1
        
    if months < 0:
        months += 12
        years -= 1
        
    parts = []
    if years > 0:
        parts.append(f"{years} Year{'s' if years != 1 else ''}")
        if months > 0:
            parts.append(f"{months} Month{'s' if months != 1 else ''}")
    else:
        if months > 0:
            parts.append(f"{months} Month{'s' if months != 1 else ''}")
            if days > 0:
                parts.append(f"{days} Day{'s' if days != 1 else ''}")
        else:
            if days > 0:
                parts.append(f"{days} Day{'s' if days != 1 else ''}")
            else:
                parts.append("Joined Today")
                
    return ", ".join(parts)

def format_user_id(user_id: int) -> str:
    """
    Formats a Discord user ID into chunks like a driver's license (e.g. 1234-5678-9012-3456-78).
    """
    s = str(user_id)
    chunks = [s[i:i+4] for i in range(0, len(s), 4)]
    return "-".join(chunks)

def generate_passport_image(
    template_path: Path,
    font_reg_path: Path,
    font_bold_path: Path,
    avatar_bytes: bytes,
    name: str,
    joined_str: str,
    residency_str: str,
    occupation: str,
    user_id_str: str,
    header: str,
    subtitle: str,
    accent_color_hex: str
) -> io.BytesIO:
    """
    Synchronous image generation logic using Pillow.
    Runs inside a thread pool executor to avoid blocking the asyncio event loop.
    """
    # Open template image
    if template_path.exists():
        img = Image.open(template_path).convert("RGBA")
    else:
        # Create a fallback dark green gradient background if template is missing
        img = Image.new("RGBA", (800, 500), (10, 24, 15))
        draw_bg = ImageDraw.Draw(img)
        # Subtle border
        draw_bg.rectangle((0, 0, 800, 500), outline=(0, 255, 102), width=5)

    # Process avatar
    try:
        avatar = Image.open(io.BytesIO(avatar_bytes)).convert("RGBA")
        avatar = avatar.resize((220, 220), Image.Resampling.LANCZOS)
    except Exception:
        # Fallback placeholder avatar
        avatar = Image.new("RGBA", (220, 220), (30, 30, 30))
        ad = ImageDraw.Draw(avatar)
        ad.rounded_rectangle((0, 0, 220, 220), radius=15, fill=(40, 40, 40), outline=(0, 255, 102), width=3)

    # Create rounded corners mask for the avatar
    mask = Image.new("L", (220, 220), 0)
    md = ImageDraw.Draw(mask)
    md.rounded_rectangle((0, 0, 220, 220), radius=15, fill=255)

    # Paste the avatar onto the template
    img.paste(avatar, (50, 130), mask=mask)

    # Parse accent color
    accent_color = accent_color_hex.strip()
    if not accent_color.startswith("#"):
        accent_color = f"#{accent_color}"

    draw = ImageDraw.Draw(img)

    # Helper to draw text and auto-scale font size if text is too long
    def draw_text_fit(text, x, y, font_file, max_size, max_width, fill):
        size = max_size
        font = None
        if font_file.exists():
            try:
                font = ImageFont.truetype(str(font_file), size)
                # Auto-scale font size to fit width
                bbox = draw.textbbox((x, y), text, font=font)
                w = bbox[2] - bbox[0]
                while w > max_width and size > 8:
                    size -= 1
                    font = ImageFont.truetype(str(font_file), size)
                    bbox = draw.textbbox((x, y), text, font=font)
                    w = bbox[2] - bbox[0]
            except Exception:
                font = None
        
        if font is None:
            font = ImageFont.load_default()
            
        draw.text((x, y), text, font=font, fill=fill)

    # Draw Header and Subtitle at the top right
    draw_text_fit(header, 310, 50, font_bold_path, 36, 450, "#FFFFFF")
    draw_text_fit(subtitle, 310, 95, font_bold_path, 16, 450, accent_color)

    # Define the 5 resident fields
    fields = [
        ("FULL NAME", name.upper(), True),
        ("DATE JOINED", joined_str, False),
        ("RESIDENCY", residency_str, False),
        ("OCCUPATION", occupation, False),
        ("CITIZEN ID NO", user_id_str, True)
    ]

    y_start = 135
    y_offset = 64

    for i, (label, val, highlight) in enumerate(fields):
        y_label = y_start + (i * y_offset)
        y_val = y_label + 18

        # Draw Label (small, Montserrat-Bold, muted sage-green color)
        draw_text_fit(label, 310, y_label, font_bold_path, 11, 450, "#88A090")

        # Draw Value
        val_color = accent_color if highlight else "#FFFFFF"
        val_font = font_bold_path if highlight else font_reg_path
        val_size = 18 if label != "FULL NAME" else 20

        draw_text_fit(val, 310, y_val, val_font, val_size, 450, val_color)

    # Save image to bytes buffer
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return buf


class KermiePassport(commands.Cog):
    """
    Generates custom citizen identification cards / resident passports for Kermieville.
    """

    def __init__(self, bot):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=1781489677509, force_registration=True)

        default_guild = {
            "default_occupation": "Citizen",
            "custom_header": "KERMIEVILLE",
            "custom_subtitle": "RESIDENT ID",
            "card_color": "#00FF66"
        }
        self.config.register_guild(**default_guild)

    async def cog_load(self):
        """Pre-download fonts asynchronously when the cog is loaded."""
        await self.check_fonts()

    async def check_fonts(self):
        """Ensures Montserrat fonts are present in the assets folder."""
        cog_dir = Path(__file__).parent
        assets_dir = cog_dir / "assets"
        assets_dir.mkdir(exist_ok=True)

        fonts = {
            "Montserrat-Regular.ttf": "https://github.com/JulietaUla/Montserrat/raw/master/fonts/ttf/Montserrat-Regular.ttf",
            "Montserrat-Bold.ttf": "https://github.com/JulietaUla/Montserrat/raw/master/fonts/ttf/Montserrat-Bold.ttf"
        }

        async with aiohttp.ClientSession() as session:
            for font_name, url in fonts.items():
                font_path = assets_dir / font_name
                if not font_path.exists():
                    try:
                        async with session.get(url) as response:
                            if response.status == 200:
                                data = await response.read()
                                font_path.write_bytes(data)
                    except Exception:
                        # Fail silently, fallback to default fonts in rendering
                        pass

    def _get_color(self, hex_str: str) -> discord.Color:
        """Helper to convert hex string to discord.Color."""
        try:
            clean_hex = hex_str.lstrip("#")
            return discord.Color(int(clean_hex, 16))
        except Exception:
            return discord.Color.green()

    @commands.command(name="passport", aliases=["license"])
    @commands.guild_only()
    async def passport(self, ctx: commands.Context, *, member: discord.Member = None):
        """
        Generates a custom Kermieville Resident ID card.
        
        Provide a member to view their ID, or run without arguments to view your own.
        """
        if member is None:
            member = ctx.author

        async with ctx.typing():
            # Get settings from config
            config = await self.config.guild(ctx.guild).all()
            
            # Format Joined Date
            joined_at = member.joined_at
            if joined_at:
                joined_str = joined_at.strftime("%B %d, %Y")
                residency_str = get_residency_duration(joined_at)
            else:
                joined_str = "Unknown"
                residency_str = "N/A"

            # Determine Occupation (highest role excluding @everyone)
            roles = [r for r in member.roles if not r.is_default()]
            if roles:
                occupation = roles[-1].name
            else:
                occupation = config["default_occupation"]

            # Format Discord ID
            user_id_str = format_user_id(member.id)

            # Paths for Pillow processing
            cog_dir = Path(__file__).parent
            assets_dir = cog_dir / "assets"
            template_path = assets_dir / "template.png"
            font_reg_path = assets_dir / "Montserrat-Regular.ttf"
            font_bold_path = assets_dir / "Montserrat-Bold.ttf"

            # Read avatar bytes
            try:
                avatar_bytes = await member.display_avatar.read()
            except Exception:
                avatar_bytes = b""

            # Run Pillow image generation in a separate executor thread
            loop = asyncio.get_running_loop()
            try:
                image_buf = await loop.run_in_executor(
                    None,
                    generate_passport_image,
                    template_path,
                    font_reg_path,
                    font_bold_path,
                    avatar_bytes,
                    member.display_name,
                    joined_str,
                    residency_str,
                    occupation,
                    user_id_str,
                    config["custom_header"],
                    config["custom_subtitle"],
                    config["card_color"]
                )
                
                # Send the generated image file
                file = discord.File(fp=image_buf, filename=f"passport_{member.id}.png")
                await ctx.send(
                    content=f"🪪 Here is the resident ID card for **{member.display_name}**!",
                    file=file
                )
            except Exception as e:
                await ctx.send(f"❌ An error occurred while generating the ID card: {e}")

    @commands.group(name="passportset")
    @commands.guild_only()
    @commands.admin_or_permissions(manage_guild=True)
    async def passportset(self, ctx: commands.Context):
        """Configuration settings for the KermiePassport cog."""
        pass

    @passportset.command(name="defaultjob")
    async def passportset_defaultjob(self, ctx: commands.Context, *, title: str):
        """Set the default occupation title for members with no roles."""
        await self.config.guild(ctx.guild).default_occupation.set(title)
        await ctx.send(f"✅ Default occupation has been set to: `{title}`")

    @passportset.command(name="header")
    async def passportset_header(self, ctx: commands.Context, *, title: str):
        """Set the main header text at the top of the ID card."""
        if len(title) > 20:
            await ctx.send("⚠️ Warning: Headers longer than 20 characters may get truncated or heavily downscaled.")
        await self.config.guild(ctx.guild).custom_header.set(title)
        await ctx.send(f"✅ Card header text has been set to: `{title}`")

    @passportset.command(name="subtitle")
    async def passportset_subtitle(self, ctx: commands.Context, *, title: str):
        """Set the subtitle text at the top of the ID card."""
        if len(title) > 25:
            await ctx.send("⚠️ Warning: Subtitles longer than 25 characters may get truncated or heavily downscaled.")
        await self.config.guild(ctx.guild).custom_subtitle.set(title)
        await ctx.send(f"✅ Card subtitle text has been set to: `{title}`")

    @passportset.command(name="color")
    async def passportset_color(self, ctx: commands.Context, hex_color: str):
        """Set the primary accent hex color for the ID card (e.g. #00FF66)."""
        hex_color = hex_color.strip()
        if not hex_color.startswith("#"):
            hex_color = f"#{hex_color}"
        
        if len(hex_color) != 7 or not all(c in "0123456789ABCDEFabcdef" for c in hex_color[1:]):
            return await ctx.send("❌ Invalid hex color format. Please use `#RRGGBB` format.")

        await self.config.guild(ctx.guild).card_color.set(hex_color)
        await ctx.send(f"✅ Card accent color has been set to: `{hex_color}`")

    @passportset.command(name="show")
    async def passportset_show(self, ctx: commands.Context):
        """Show current configuration settings for this server."""
        config = await self.config.guild(ctx.guild).all()
        color = self._get_color(config["card_color"])
        
        embed = discord.Embed(
            title="🪪 KermiePassport Configuration",
            color=color
        )
        embed.add_field(name="Header", value=config["custom_header"], inline=True)
        embed.add_field(name="Subtitle", value=config["custom_subtitle"], inline=True)
        embed.add_field(name="Default Job", value=config["default_occupation"], inline=True)
        embed.add_field(name="Accent Color", value=f"`{config['card_color']}`", inline=True)
        embed.set_footer(text=f"Prefix: {ctx.clean_prefix}")
        await ctx.send(embed=embed)
