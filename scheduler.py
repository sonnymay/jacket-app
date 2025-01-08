
from apscheduler.schedulers.blocking import BlockingScheduler
from app import send_daily_weather_update
from pytz import timezone

scheduler = BlockingScheduler()
scheduler.add_job(
    func=send_daily_weather_update,
    trigger='cron',
    hour=7,
    minute=30,
    timezone=timezone("America/Chicago")
)

if __name__ == "__main__":
    scheduler.start()