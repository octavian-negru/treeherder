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
rootUrl = "https://taskcluster.net"
options = {"rootUrl": rootUrl}
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


async def handleTask(taskId):
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
            # In handleMessage() if we have a pending task and runId > 0 we resolve
            # the previous task as retried.
            # If we don't this here we will have the previous run (run==0) as "exception" (purple)
            # rather than "retry" (blue)
            # XXX: Think this well and see if it makes sense
            # Maybe check "runs" -> # -> reasonResolved === "intermitent-task"
            # https://queue.taskcluster.net/v1/task/W_NSZcNPQxqIcOc9XN8sSw/status
            if len(runs) > 1 and run["runId"] != len(runs) - 1:
                tc_th_message["isRetried"] = True

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
        asyncTasks.append(asyncio.create_task(handleTask(task["task_id"])))

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
            help="changeset to import"
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
            loop.run_until_complete(handleTask(taskId))
        else:
            # project = options["project"]
            # changeset = options["changeset"]
            # XXX: We need to use the task group ID to find "action request" tasks
            # taskGroupId = 'cb3srnG9QJC5iq5f8m55Rw'
            # XXX: For local development we hardcode to a specific push
            geckoDecisionTaskId = 'cb3srnG9QJC5iq5f8m55Rw'
            task_graph_url = "https://taskcluster-artifacts.net/{}/0/public/task-graph.json".format(geckoDecisionTaskId)
            logger.info("## START ##")
            graph = requests.get(task_graph_url).json()
            # Let's not forget to ingest the Gecko decision task
            graph[geckoDecisionTaskId] = { 'task_id': geckoDecisionTaskId }
            loop.run_until_complete(handleTasks(graph))
            logger.info("## END ##")
