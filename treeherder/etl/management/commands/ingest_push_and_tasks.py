import asyncio
import logging

import aiohttp
import taskcluster
import taskcluster.aio
from django.core.management.base import BaseCommand

from treeherder.etl.job_loader import JobLoader
from treeherder.etl.taskcluster_pulse.handler import (EXCHANGE_EVENT_MAP,
                                                      handleMessage)

logger = logging.getLogger(__name__)
rootUrl = "https://taskcluster.net"
options = {"rootUrl": rootUrl}
loop = asyncio.get_event_loop()
# Limiting the connection pool just in case we have too many
conn = aiohttp.TCPConnector(limit=60)
# Remove default timeout limit of 5 minutes
timeout = aiohttp.ClientTimeout(total=0)
session = taskcluster.aio.createSession(loop=loop, connector=conn, timeout=timeout)
asyncQueue = taskcluster.aio.Queue(options, session=session)

stateToExchange = {}
for key, value in EXCHANGE_EVENT_MAP.items():
    stateToExchange[value] = key


async def handleTaskId(taskId):
    results = await asyncio.gather(asyncQueue.status(taskId), asyncQueue.task(taskId))
    await handleTask({
        "status": results[0]["status"],
        "task": results[1],
    })


async def handleTask(task):
    taskId = task["status"]["taskId"]
    runs = task["status"]["runs"]
    for run in runs:
        message = {
            "exchange": stateToExchange[run["state"]],
            "payload": {
                "status": {
                    "taskId": taskId,
                    "runs": runs,
                },
                "runId": run["runId"],
            }
        }
        try:
            tc_th_message = await handleMessage(message, task["task"])
            if tc_th_message:
                logger.info("Loading into DB:\t%s/%s", taskId, run["runId"])
                # XXX: This seems our current bottleneck
                JobLoader().process_job(tc_th_message)
        except Exception as e:
            logger.exception(e)


async def fetchGroupTasks(taskGroupId):
    tasks = []
    query = {}
    continuationToken = ""
    while True:
        if continuationToken:
            query = {"continuationToken": continuationToken}
        response = await asyncQueue.listTaskGroup(taskGroupId, query=query)
        tasks.extend(response['tasks'])
        continuationToken = response.get('continuationToken')
        if continuationToken is None:
            break
        logger.info('Requesting more tasks. %s tasks so far...', len(tasks))
    return tasks


async def processTasks(taskGroupId):
    tasks = await fetchGroupTasks(taskGroupId)
    asyncTasks = []
    logger.info("We have %s tasks to process", len(tasks))
    for task in tasks:
        asyncTasks.append(asyncio.create_task(handleTask(task)))

    await asyncio.gather(*asyncTasks)


class Command(BaseCommand):
    """Management command to ingest data from a single push."""
    help = "Ingests a single push and tasks into Treeherder"

    def add_arguments(self, parser):
        parser.add_argument(
            "--project",
            help="repository to query"
        )
        parser.add_argument(
            "--revision",
            nargs="?",
            help="revision to import"
        )
        parser.add_argument(
            "--task-id",
            dest="taskId",
            nargs="?",
            help="taskId to ingest"
        )

    def handle(self, *args, **options):
        taskId = options["taskId"]
        if taskId:
            loop.run_until_complete(handleTaskId(taskId))
        else:
            # project = options["project"]
            # revision = options["revision"]
            # XXX: Need logic to get from project/revision to taskGroupId
            taskGroupId = 'cb3srnG9QJC5iq5f8m55Rw'
            logger.info("## START ##")
            loop.run_until_complete(processTasks(taskGroupId))
            logger.info("## END ##")
