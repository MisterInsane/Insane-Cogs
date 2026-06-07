from .customcogs import CustomCogs

async def setup(bot):
    """
    The setup function for the CustomCogs cog.
    This is called by Redbot to load the cog.
    """
    await bot.add_cog(CustomCogs(bot))
