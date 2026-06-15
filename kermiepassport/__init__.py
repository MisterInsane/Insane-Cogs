from .passport import KermiePassport

async def setup(bot):
    """
    Setup function for the KermiePassport cog.
    """
    await bot.add_cog(KermiePassport(bot))
