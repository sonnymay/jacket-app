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
    """Log job execution status."""
    if event.exception:
        logger.error(f'[JOB] Failed: {event.job_id}')
        logger.error(f'[JOB] Exception: {event.exception}')
        logger.error(f'[JOB] Traceback: {event.traceback}')
    else:
        logger.info(f'[JOB] Completed: {event.job_id}')
        logger.info(f'[JOB] Next run: {event.job.next_run_time}')

# Initialize scheduler
scheduler = BlockingScheduler(timezone=timezone('America/Chicago'))
scheduler.add_listener(log_job_status)

if __name__ == "__main__":
    logger.info("[WORKER] Starting scheduler process")
    logger.info(f"[WORKER] Process ID: {os.getpid()}")
    
    try:
        # Start the scheduler first
        scheduler.start()
        
        # Add the daily update job
        job = scheduler.add_job(
            func=send_daily_weather_update,
            trigger='cron',
            hour=7,
            minute=30,
            id='daily_weather_job',
            name='Daily Weather Update',
            replace_existing=True
        )
        logger.info(f"[WORKER] Daily job scheduled: {job}")
        logger.info(f"[WORKER] Next run time: {job.next_run_time}")
        
        # Add test job to verify setup
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
        
    except Exception as e:
        logger.error(f"[WORKER] Startup error: {e}")
        logger.exception("[WORKER] Full exception details:")
        raise