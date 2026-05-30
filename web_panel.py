"""
web_panel.py — веб-панель администратора (открывается в браузере).

Запуск:   python web_panel.py
Открой в браузере:   http://АДРЕС_СЕРВЕРА:8080
Логин/пароль — из .env (WEB_PANEL_USER / WEB_PANEL_PASSWORD). ОБЯЗАТЕЛЬНО смени пароль!

Что показывает и умеет:
  • таблицу всех клиентов: срок, трафик, скорость, профиль протоколов;
  • список оплат (журнал платежей) и последние события;
  • продлить подписку клиенту;
  • поменять лимиты (трафик/скорость/профиль) — изменения скорости сразу применяются;
  • отключить клиента.

Подтверждать оплаты удобнее в самом боте (там клиенту сразу уходит ключ).
Здесь панель — для наблюдения и ручного управления лимитами/сроками.
"""

import secrets

import uvicorn
from fastapi import Depends, FastAPI, Form, HTTPException, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials

import config
import database as db
import traffic_control as tc
import vpn_manager

app = FastAPI(title="VPN — панель администратора")
security = HTTPBasic()


def check_auth(credentials: HTTPBasicCredentials = Depends(security)) -> str:
    """Простая защита логином/паролем (HTTP Basic). Сравниваем безопасно."""
    user_ok = secrets.compare_digest(credentials.username, config.WEB_PANEL_USER)
    pass_ok = secrets.compare_digest(credentials.password, config.WEB_PANEL_PASSWORD)
    if not (user_ok and pass_ok):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Неверный логин или пароль",
            headers={"WWW-Authenticate": "Basic"},
        )
    return credentials.username


def _esc(text) -> str:
    """Экранируем текст, чтобы имена/логины не ломали HTML-страницу."""
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


PAGE_STYLE = """
<style>
  body { font-family: system-ui, sans-serif; margin: 24px; background:#f6f7f9; color:#1c1e21; }
  h1,h2 { color:#0b3d91; }
  table { border-collapse: collapse; width: 100%; background:#fff; margin-bottom:28px; }
  th,td { border:1px solid #dadde1; padding:8px 10px; text-align:left; font-size:14px; }
  th { background:#eef1f6; }
  form.inline { display:inline; }
  input,select,button { padding:6px 8px; font-size:13px; }
  .muted { color:#65676b; font-size:13px; }
  .pill { padding:2px 8px; border-radius:10px; font-size:12px; }
  .active { background:#e3f7e8; color:#1a7f37; }
  .expired,.inactive { background:#fdecea; color:#b3261e; }
</style>
"""


@app.get("/", response_class=HTMLResponse)
def dashboard(_: str = Depends(check_auth)) -> str:
    subs = db.list_subscriptions()
    payments = db.list_payments(50)
    events = db.list_events(30)

    # --- таблица клиентов с формами управления ---
    rows = ""
    for s in subs:
        who = _esc(s["username"] or s["full_name"] or s["user_id"])
        status_class = s["status"]
        limit_gb = s["traffic_limit_mb"] // 1024 if s["traffic_limit_mb"] else 0
        used_gb = s["traffic_used_mb"] / 1024
        rows += f"""
        <tr>
          <td>{who}<div class="muted">ID {s['user_id']} · {_esc(s['wg_ip'] or '—')}</div></td>
          <td><span class="pill {status_class}">{s['status']}</span></td>
          <td>{db.ts_to_str(s['expires_at'])}</td>
          <td>{used_gb:.1f} / {limit_gb if limit_gb else '∞'} ГБ</td>
          <td>{s['speed_limit_mbit'] or '∞'} Мбит/с</td>
          <td>{_esc(s['protocol_profile'])}</td>
          <td>
            <form class="inline" method="post" action="/extend">
              <input type="hidden" name="user_id" value="{s['user_id']}">
              <input type="number" name="days" value="30" style="width:60px"> дн.
              <button type="submit">Продлить</button>
            </form>
          </td>
          <td>
            <form class="inline" method="post" action="/limit">
              <input type="hidden" name="user_id" value="{s['user_id']}">
              <input type="number" name="traffic_gb" value="{limit_gb}" style="width:60px" title="ГБ">
              <input type="number" name="speed" value="{s['speed_limit_mbit']}" style="width:60px" title="Мбит/с">
              <select name="profile">
                {_profile_options(s['protocol_profile'])}
              </select>
              <button type="submit">Сохранить</button>
            </form>
          </td>
          <td>
            <form class="inline" method="post" action="/disable"
                  onsubmit="return confirm('Отключить клиента {s['user_id']}?')">
              <input type="hidden" name="user_id" value="{s['user_id']}">
              <button type="submit">Отключить</button>
            </form>
          </td>
        </tr>"""

    # --- журнал платежей ---
    pay_rows = ""
    for p in payments:
        who = _esc(p["username"] or p["full_name"] or p["user_id"])
        pay_rows += f"""
        <tr>
          <td>#{p['id']}</td><td>{who}</td><td>{p['amount']} ₽</td>
          <td>{p['plan_days']} дн.</td><td>{_esc(p['method'])}</td>
          <td>{_esc(p['status'])}</td><td>{db.ts_to_str(p['created_at'])}</td>
        </tr>"""

    # --- последние события ---
    ev_rows = ""
    for e in events:
        ev_rows += f"<tr><td>{db.ts_to_str(e['ts'])}</td><td>{_esc(e['level'])}</td><td>{_esc(e['message'])}</td></tr>"

    return f"""
    <!doctype html><html lang="ru"><head><meta charset="utf-8">
    <title>VPN — панель</title>{PAGE_STYLE}</head><body>
    <h1>VPN — панель администратора</h1>
    <p class="muted">Режим применения правил на сервере:
        <b>{'ВКЛЮЧЁН' if config.APPLY_TRAFFIC_RULES else 'выключен (только показ)'}</b></p>

    <h2>Клиенты</h2>
    <table>
      <tr><th>Клиент</th><th>Статус</th><th>До</th><th>Трафик</th><th>Скорость</th>
          <th>Протоколы</th><th>Продлить</th><th>Лимиты</th><th></th></tr>
      {rows or '<tr><td colspan="9">Пока нет клиентов</td></tr>'}
    </table>

    <h2>Платежи (последние 50)</h2>
    <table>
      <tr><th>#</th><th>Клиент</th><th>Сумма</th><th>Срок</th><th>Способ</th><th>Статус</th><th>Создан</th></tr>
      {pay_rows or '<tr><td colspan="7">Платежей нет</td></tr>'}
    </table>

    <h2>События (последние 30)</h2>
    <table>
      <tr><th>Время</th><th>Уровень</th><th>Сообщение</th></tr>
      {ev_rows or '<tr><td colspan="3">Событий нет</td></tr>'}
    </table>
    </body></html>"""


def _profile_options(current: str) -> str:
    opts = ""
    for prof in tc.PROTOCOL_PROFILES:
        selected = "selected" if prof == current else ""
        opts += f'<option value="{prof}" {selected}>{prof}</option>'
    return opts


@app.post("/extend")
def extend(user_id: int = Form(...), days: int = Form(...), _: str = Depends(check_auth)):
    # Если ключа ещё нет — создадим, чтобы было что продлевать.
    vpn_manager.issue_key_for_user(
        user_id,
        config.DEFAULT_TRAFFIC_LIMIT_GB * 1024,
        config.DEFAULT_SPEED_LIMIT_MBIT,
        config.DEFAULT_PROTOCOL_PROFILE,
    )
    new_expires = db.extend_subscription(user_id, days)
    db.log_event("info", f"[панель] подписка {user_id} продлена до {db.ts_to_str(new_expires)}")
    return RedirectResponse("/", status_code=303)


@app.post("/limit")
def limit(
    user_id: int = Form(...),
    traffic_gb: int = Form(...),
    speed: int = Form(...),
    profile: str = Form(...),
    _: str = Depends(check_auth),
):
    if profile not in tc.PROTOCOL_PROFILES:
        profile = "all"
    db.set_limits(user_id, traffic_gb * 1024, speed, profile)
    sub = db.get_subscription(user_id)
    if sub and sub["wg_ip"]:
        tc.set_speed_limit(sub["wg_ip"], speed)
    db.log_event("info", f"[панель] лимиты {user_id}: {traffic_gb}ГБ/{speed}Мбит/{profile}")
    return RedirectResponse("/", status_code=303)


@app.post("/disable")
def disable(user_id: int = Form(...), _: str = Depends(check_auth)):
    sub = db.get_subscription(user_id)
    if sub:
        if sub["wg_public_key"]:
            vpn_manager.remove_peer(sub["wg_public_key"], apply=config.APPLY_TRAFFIC_RULES)
        if sub["wg_ip"]:
            tc.set_speed_limit(sub["wg_ip"], 0)
        db.set_status(user_id, "expired")
        db.log_event("warning", f"[панель] клиент {user_id} отключён вручную")
    return RedirectResponse("/", status_code=303)


if __name__ == "__main__":
    db.init_db()
    print(f"Панель: http://0.0.0.0:{config.WEB_PANEL_PORT}  (логин: {config.WEB_PANEL_USER})")
    uvicorn.run(app, host="0.0.0.0", port=config.WEB_PANEL_PORT)
