from .moderation import ModSlash

async def setup(bot):
    """
    The setup function for the ModSlash cog.
    This is called by Redbot to load the cog.
    """
    await bot.add_cog(ModSlash(bot))
