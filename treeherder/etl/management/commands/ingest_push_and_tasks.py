import asyncio
import logging

import aiohttp
import requests
import taskcluster
import taskcluster.aio
from django.core.management.base import BaseCommand

from treeherder.etl.job_loader import JobLoader
from treeherder.etl.taskcluster_pulse.handler import (EXCHANGE_EVENT_MAP,
                                                      handleMessage)

logger = logging.getLogger(__name__)
options = {"rootUrl": "https://taskcluster.net"}
loop = asyncio.get_event_loop()
# Limiting the connection pool just in case we have too many
conn = aiohttp.TCPConnector(limit=30)
# Remove default timeout limit of 5 minutes
timeout = aiohttp.ClientTimeout(total=0)
session = taskcluster.aio.createSession(loop=loop, connector=conn, timeout=timeout)
asyncQueue = taskcluster.aio.Queue(options, session=session)

stateToExchange = {}
for key, value in EXCHANGE_EVENT_MAP.items():
    stateToExchange[value] = key


async def handleTask(task):
    taskId = task["task_id"]
    results = await asyncio.gather(asyncQueue.status(taskId), asyncQueue.task(taskId))
    taskStatus = results[0]
    taskDefinition = results[1]
    runs = taskStatus["status"]["runs"]
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
            tc_th_message = await handleMessage(message, taskDefinition)
            if tc_th_message:
                logger.info("Loading into DB:\t%s/%s", taskId, run["runId"])
                JobLoader().process_job(tc_th_message)
        except Exception as e:
            logger.exception(e)


async def handleTasks(graph):
    asyncTasks = []
    tasks = list(graph.values())
    logger.info("We have %s tasks to process", len(tasks))
    for task in tasks:
        asyncTasks.append(asyncio.create_task(handleTask(task)))

    await asyncio.gather(*asyncTasks)


class Command(BaseCommand):
    """Management command to ingest data from a single push."""
    help = "Ingests a single push and tasks into Treeherder"

    def add_arguments(self, parser):
        parser.add_argument(
            "project",
            help="repository to query"
        )
        parser.add_argument(
            "changeset",
            nargs="?",
            help="changeset to import"
        )

    def handle(self, *args, **options):
        # project = options["project"]
        # changeset = options["changeset"]
        # XXX: For local development we hardcode to a specific push
        task_graph_url = "https://taskcluster-artifacts.net/cb3srnG9QJC5iq5f8m55Rw/0/public/task-graph.json"
        logger.info("## START ##")
        graph = requests.get(task_graph_url).json()
        loop.run_until_complete(handleTasks(graph))
        logger.info("## END ##")
