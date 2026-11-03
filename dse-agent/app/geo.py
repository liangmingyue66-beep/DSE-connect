"""省份/城市归一（确定性校验）。

why: 模型可能输出"华东省""广州"等非法省份。省份是**枚举值**（库里 23 个），
     可以硬校验；不像专业词无法枚举。城市→省份映射直接从库生成，不手写全国表。
"""
from app.db import get_conn

_VALID_PROVINCES: set[str] | None = None
_CITY_TO_PROVINCE: dict[str, str] | None = None


def _load() -> None:
    """从库加载省份集合与城市映射（懒加载一次）。"""
    global _VALID_PROVINCES, _CITY_TO_PROVINCE
    if _VALID_PROVINCES is not None:
        return
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT DISTINCT province FROM programmes WHERE province IS NOT NULL")
            _VALID_PROVINCES = {r[0] for r in cur.fetchall()}
            # 只取单城市行：排除"广州、佛山、汕尾"这类多城市串（避免映射错）
            cur.execute(
                "SELECT DISTINCT city, province FROM programmes "
                "WHERE city IS NOT NULL AND city <> '' AND city NOT LIKE %s",
                ("%\\、%",),
            )
            _CITY_TO_PROVINCE = {c.strip(): p for c, p in cur.fetchall() if c and p}
    finally:
        conn.close()


def normalize_province(p: str | None) -> tuple[str | None, str | None]:
    """归一省份；返回 (规范省份, 错误提示)。无法识别时省份为 None、err 有值。"""
    _load()
    p = (p or "").strip()
    if not p:
        return None, None
    if p in _VALID_PROVINCES:
        return p, None
    for suffix in ("省", "市", "自治区"):
        if p + suffix in _VALID_PROVINCES:
            return p + suffix, None
    if p in _CITY_TO_PROVINCE:
        return _CITY_TO_PROVINCE[p], None
    # 唯一前缀匹配："广西" → "广西壮族自治区"
    hits = [x for x in _VALID_PROVINCES if x.startswith(p)]
    if len(hits) == 1:
        return hits[0], None
    return None, (
        f"省份「{p}」无法识别。请确认用户说的是哪个省级行政区"
        f"（例如：广东省、北京市、广西壮族自治区），不要自行猜测。"
    )


def province_of_city(city: str | None) -> str | None:
    """城市 → 省份（仅单城市行；多城市串返回 None，不猜测）。"""
    _load()
    return _CITY_TO_PROVINCE.get((city or "").strip())
