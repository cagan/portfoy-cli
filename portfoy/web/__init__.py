"""Portfoyun web arayuzu.

CLI ile AYNI is mantigini kullanir: `portfoy.storage`, `portfoy.analytics`,
`portfoy.charts`, `portfoy.mailer`. Burada yalnizca sunum, dogrulama ve es
zamanlilik kabuğu vardir - hesap kurallarinin ikinci bir kopyasi yoktur, cunku
iki kopya kacinilmaz olarak birbirinden ayrilir.

Giris noktasi: `portfoy web` (bkz. portfoy/cli.py -> cmd_web) veya

    from portfoy.web import create_app
    uvicorn.run(create_app())
"""

from .app import create_app, run

__all__ = ["create_app", "run"]
