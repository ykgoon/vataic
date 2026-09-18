"""Worker entrypoint: connects to Temporal server from env, runs ProjectionJobWorkflow."""
import asyncio

def main() -> None:
    from app.config import settings
    try:
        from temporalio.client import Client
        from temporalio.worker import Worker
        from worker.workflows import ProjectionJobWorkflow, ACTIVITIES
    except ImportError:
        print("temporalio not installed; worker cannot start")
        return

    async def _run():
        client = await Client.connect(settings.temporal_address, namespace=settings.temporal_namespace)
        worker = Worker(client, task_queue=settings.temporal_task_queue,
                        workflows=[ProjectionJobWorkflow], activities=ACTIVITIES)
        print(f"worker on {settings.temporal_address}/{settings.temporal_task_queue}")
        await worker.run()

    asyncio.run(_run())

if __name__ == "__main__":
    main()
