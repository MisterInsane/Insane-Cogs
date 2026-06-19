from .massvoice import MassVoice

async def setup(bot):
    """
    The setup function for the MassVoice cog.
    This is called by Redbot to load the cog.
    """
    cog = MassVoice(bot)
    await bot.add_cog(cog)
