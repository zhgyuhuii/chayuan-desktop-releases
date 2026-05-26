"""多用户鉴权子系统。

- ``auth.password`` 密码哈希；
- ``auth.tokens``   JWT 签验；
- ``auth.service``  用户 CRUD / 登录；
- ``auth.access``   KB 访问控制；
- ``auth.deps``     FastAPI dependencies；
- ``auth.middleware`` JWT 解析中间件。
"""

from chayuan.server.auth.deps import (  # noqa: F401
    get_current_user,
    get_current_user_optional,
    require_role,
)
from chayuan.server.auth.middleware import AuthMiddleware  # noqa: F401
