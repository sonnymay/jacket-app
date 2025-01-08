from apscheduler.schedulers.blocking import BlockingScheduler
from app import send_daily_weather_update
from pytz import timezone
import logging

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

scheduler = BlockingScheduler()
scheduler.add_job(
    func=send_daily_weather_update,
    trigger='cron',
    hour=7,
    minute=30,
    timezone=timezone("America/Chicago"),
    id='daily_weather_job',
    name='Daily Weather Update'
)

if __name__ == "__main__":
    logger.info("Starting scheduler...")
    try:
        scheduler.start()
    except Exception as e:
        logger.error(f"Scheduler error: {str(e)}")