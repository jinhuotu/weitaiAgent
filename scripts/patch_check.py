import pathlib
p = pathlib.Path("E:/code/python/weitaiAgent/apps/api/services/prompts/configs.py")
t = p.read_text(encoding="utf-8")
old = 'async def list_options(db: AsyncSession) -> list[dict[str, Any]]:'
assert old in t, "anchor not found"
print("anchor ok", len(t))
