from .customhelp import CustomHelp

async def setup(bot):
    """
    The setup function for the CustomHelp cog.
    This is called by Redbot to load the cog.
    """
    await bot.add_cog(CustomHelp(bot))
