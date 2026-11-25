"""Optional reminders and daily learning backups while the browser is closed.

Run in a separate PyCharm configuration. The process must remain running; this
does not install an OS startup task or promise notifications after shutdown.
"""
import argparse
import time
from app import create_app
from newsreader.db import today
from newsreader.study_plans import claim_reminders
from newsreader.data_management import flag,manager


def tick(app,notify):
    with app.test_request_context('/api/planning/reminders/claim',method='POST',json={}):
        response=claim_reminders().get_json()
        for item in response['reminders']:
            message='今日阅读目标还未完成，读一小段继续积累。' if item['kind']=='reading' else f"有 {item['due']} 个词待复习。"
            try:notify(message)
            except Exception:
                # A failed desktop notification remains claimable on the next run.
                from newsreader.db import get_db
                db=get_db();db.execute('DELETE FROM study_reminder_days WHERE day=? AND kind=?',(response['day'],item['kind']));db.commit()
                raise
        if flag('data.auto_backup'):
            control=manager();backups=control.list_backups();day=today().isoformat()
            if not any(x['kind']=='auto' and x['created_at'].startswith(day) for x in backups):
                control.backup('auto');control.cleanup()
        return response


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database',help='可指定本机账户的 learning.sqlite3')
    parser.add_argument('--once',action='store_true',help='只检查一次，便于用户自行配置系统任务')
    parser.add_argument('--console',action='store_true',help='仅输出通知文字，便于自检')
    args=parser.parse_args();app=create_app({'DATABASE':args.database} if args.database else None)
    if args.console:notify=print
    else:
        try:from plyer import notification
        except ImportError:raise SystemExit('请先安装通知依赖：python -m pip install -r requirements-background.txt')
        def notify(message):notification.notify(title='Margin 学习提醒',message=message,app_name='Margin',timeout=10)
    while True:
        try:tick(app,notify)
        except Exception as error:print('后台检查失败：'+type(error).__name__,flush=True)
        if args.once:return
        time.sleep(30)


if __name__=='__main__':main()
