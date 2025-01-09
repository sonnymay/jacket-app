from apscheduler.schedulers.blocking import BlockingScheduler
from app import send_daily_weather_update
from pytz import timezone, utc
import logging
from datetime import datetime, timedelta
import os

# Configure detailed logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

def log_job_status(event):
    """Enhanced job execution status logging."""
    if event.exception:
        logger.error(f'[JOB] Failed: {event.job_id}')
        logger.error(f'[JOB] Exception: {event.exception}')
        logger.error(f'[JOB] Traceback: {event.traceback}')
    else:
        logger.info(f'[JOB] Completed: {event.job_id}')
        logger.info(f'[JOB] Next run: {event.job.next_run_time}')

def convert_to_utc(local_time_str, local_tz_str="America/Chicago"):
    """Convert local time to UTC for scheduling."""
    local_tz = timezone(local_tz_str)
    local_time = datetime.strptime(local_time_str, "%H:%M").replace(tzinfo=local_tz)
    utc_time = local_time.astimezone(utc)
    return utc_time.hour, utc_time.minute

# Initialize scheduler with enhanced monitoring
scheduler = BlockingScheduler(timezone=timezone('America/Chicago'))
scheduler.add_listener(log_job_status)

# Check environment variables
logger.info("[ENV] Checking environment variables:")
for var in ['TWILIO_ACCOUNT_SID', 'TWILIO_AUTH_TOKEN', 'TWILIO_PHONE_NUMBER', 
            'OPENWEATHERMAP_API_KEY', 'OPENAI_API_KEY']:
    logger.info(f"[ENV] {var}: {'Present' if os.getenv(var) else 'Missing'}")

try:
    # Convert local time (7:30 AM CT) to UTC for scheduling
    utc_hour, utc_minute = convert_to_utc("07:30")
    logger.info(f"Scheduling job for {utc_hour:02d}:{utc_minute:02d} UTC")
    
    # Add the daily weather update job
    job = scheduler.add_job(
        func=send_daily_weather_update,
        trigger='cron',
        hour=utc_hour,
        minute=utc_minute,
        id='daily_weather_job',
        name='Daily Weather Update',
        replace_existing=True
    )
    logger.info(f"Job scheduled: {job}")
    logger.info(f"Next run time (UTC): {job.next_run_time}")
except Exception as e:
    logger.error(f"Failed to schedule job: {e}")

if __name__ == "__main__":
    logger.info("[WORKER] Starting scheduler process")
    logger.info(f"[WORKER] Process ID: {os.getpid()}")
    
    try:
        # Add test job for immediate verification
        test_job = scheduler.add_job(
            func=send_daily_weather_update,
            trigger='date',
            run_date=datetime.now() + timedelta(minutes=1),
            id='startup_test_job'
        )
        logger.info(f"[WORKER] Test job scheduled for: {test_job.next_run_time}")
        
        # Log all scheduled jobs
        logger.info("[WORKER] Currently scheduled jobs:")
        scheduler.print_jobs()
        
        scheduler.start()
    except Exception as e:
        logger.error(f"[WORKER] Startup error: {e}")
        logger.exception("[WORKER] Full exception details:")
        raise