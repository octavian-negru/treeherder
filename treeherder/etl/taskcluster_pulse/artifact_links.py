# Code imported from https://github.com/taskcluster/taskcluster/blob/32629c562f8d6f5a6b608a3141a8ee2e0984619f/services/treeherder/src/transform/artifact_links.js
import logging
import os

import taskcluster
import taskcluster_urls

logger = logging.getLogger(__name__)
root_url = "https://taskcluster.net"


def addArtifactUploadedLinks(taskId, runId, job):
    res = None
    try:
        res = taskcluster.queue.listArtifacts(taskId, runId)
    except Exception:
        logger.warning("Artifacts could not be found for task: %s run: %s", taskId, runId)
        return job

    artifacts = res["artifacts"]

    while res["continuationToken"]:
        continuation = {
          "continuationToken": res["continuationToken"]
        }

        try:
            res = taskcluster.queue.listArtifacts(taskId, runId, continuation)
        except Exception:
            break

        artifacts = artifacts.concat(res["artifacts"])

    seen = {}
    links = []
    for artifact in artifacts:
        name = os.path.basename(artifact["name"])
        if not seen[name]:
            seen[name] = [artifact["name"]]
        else:
            seen[name].append(artifact["name"])
            name = "{name} ({length})".format(name=name, length=len(seen[name])-1)

        links.append({
            "label": "artifact uploaded",
            "linkText": name,
            "url": taskcluster_urls.api(
                root_url,
                "queue",
                "v1",
                "task/{taskId}/runs/{runId}/artifacts/{artifact_name}".format(
                    taskId=taskId, runId=runId, artifact_name=artifact["name"]
                )),
        })

    job["jobInfo"]["links"] = job["jobInfo"]["links"].concat(links)

    return job
