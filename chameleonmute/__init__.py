from .chameleonmute import ChameleonMute

async def setup(bot):
    """
    The setup function for the ChameleonMute cog.
    This is called by Redbot to load the cog.
    """
    cog = ChameleonMute(bot)
    await bot.add_cog(cog)
