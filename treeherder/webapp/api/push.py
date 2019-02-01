import datetime

from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.status import (HTTP_400_BAD_REQUEST,
                                   HTTP_404_NOT_FOUND)
from six import iteritems

from treeherder.model.models import (Push,
                                     Repository)
from treeherder.webapp.api.serializers import PushSerializer
from treeherder.webapp.api.utils import (to_datetime,
                                         to_timestamp)


class PushViewSet(viewsets.ViewSet):
    """
    View for ``push`` records
    """

    def list(self, request, project):
        """
        GET method for list of ``push`` records with revisions
        """
        # What is the upper limit on the number of pushes returned by the api
        MAX_PUSH_COUNT = 1000

        # make a mutable copy of these params
        filter_params = request.query_params.copy()

        # This will contain some meta data about the request and results
        meta = {}

        # support ranges for date as well as revisions(changes) like old tbpl
        for param in ["fromchange", "tochange", "startdate", "enddate", "revision"]:
            v = filter_params.get(param, None)
            if v:
                del filter_params[param]
                meta[param] = v

        try:
            repository = Repository.objects.get(name=project)
        except Repository.DoesNotExist:
            return Response({
                "detail": "No project with name {}".format(project)
            }, status=HTTP_404_NOT_FOUND)

        pushes = Push.objects.filter(repository=repository).order_by('-time')

        for (param, value) in iteritems(meta):
            if param == 'fromchange':
                frompush_time = Push.objects.values_list('time', flat=True).get(
                    repository=repository, revision__startswith=value)
                pushes = pushes.filter(time__gte=frompush_time)
                filter_params.update({
                    "push_timestamp__gte": to_timestamp(frompush_time)
                })

            elif param == 'tochange':
                topush_time = Push.objects.values_list('time', flat=True).get(
                    repository=repository, revision__startswith=value)
                pushes = pushes.filter(time__lte=topush_time)
                filter_params.update({
                    "push_timestamp__lte": to_timestamp(topush_time)
                })
            elif param == 'startdate':
                pushes = pushes.filter(time__gte=to_datetime(value))
                filter_params.update({
                    "push_timestamp__gte": to_timestamp(to_datetime(value))
                })
            elif param == 'enddate':
                real_end_date = to_datetime(value) + datetime.timedelta(days=1)
                pushes = pushes.filter(time__lte=real_end_date)
                filter_params.update({
                    "push_timestamp__lt": to_timestamp(real_end_date)
                })
            elif param == 'revision':
                # revision can be either the revision of the push itself, or
                # any of the commits it refers to
                pushes = pushes.filter(commits__revision__startswith=value)
                rev_key = "revisions_long_revision" \
                          if len(meta['revision']) == 40 else "revisions_short_revision"
                filter_params.update({rev_key: meta['revision']})

        for param in ['push_timestamp__lt', 'push_timestamp__lte',
                      'push_timestamp__gt', 'push_timestamp__gte']:
            if filter_params.get(param):
                # translate push timestamp directly into a filter
                try:
                    value = datetime.datetime.fromtimestamp(
                        float(filter_params.get(param)))
                except ValueError:
                    return Response({
                        "detail": "Invalid timestamp specified for {}".format(
                            param)
                    }, status=HTTP_400_BAD_REQUEST)
                pushes = pushes.filter(**{
                    param.replace('push_timestamp', 'time'): value
                })

        for param in ['id__lt', 'id__lte', 'id__gt', 'id__gte', 'id']:
            try:
                value = int(filter_params.get(param, 0))
            except ValueError:
                return Response({
                    "detail": "Invalid timestamp specified for {}".format(
                        param)
                }, status=HTTP_400_BAD_REQUEST)
            if value:
                pushes = pushes.filter(**{param: value})

        id_in = filter_params.get("id__in")
        if id_in:
            try:
                id_in_list = [int(id) for id in id_in.split(',')]
            except ValueError:
                return Response({"detail": "Invalid id__in specification"},
                                status=HTTP_400_BAD_REQUEST)
            pushes = pushes.filter(id__in=id_in_list)

        author = filter_params.get("author")
        if author:
            pushes = pushes.filter(author=author)

        try:
            count = int(filter_params.get("count", 10))
        except ValueError:
            return Response({"detail": "Valid count value required"},
                            status=HTTP_400_BAD_REQUEST)

        if count > MAX_PUSH_COUNT:
            msg = "Specified count exceeds api limit: {}".format(MAX_PUSH_COUNT)
            return Response({"detail": msg}, status=HTTP_400_BAD_REQUEST)

        # we used to have a "full" parameter for this endpoint so you could
        # specify to not fetch the revision information if it was set to
        # false. however AFAIK no one ever used it (default was to fetch
        # everything), so let's just leave it out. it doesn't break
        # anything to send extra data when not required.
        pushes = pushes.select_related('repository').prefetch_related('commits')[:count]
        serializer = PushSerializer(pushes, many=True)

        meta['count'] = len(pushes)
        meta['repository'] = project
        meta['filter_params'] = filter_params

        resp = {
            'meta': meta,
            'results': serializer.data
        }

        return Response(resp)

    def retrieve(self, request, project, pk=None):
        """
        GET method implementation for detail view of ``push``
        """
        try:
            push = Push.objects.get(repository__name=project,
                                    id=pk)
            serializer = PushSerializer(push)
            return Response(serializer.data)
        except Push.DoesNotExist:
            return Response("No push with id: {0}".format(pk),
                            status=HTTP_404_NOT_FOUND)

    @action(detail=True)
    def status(self, request, project, pk=None):
        """
        Return a count of the jobs belonging to this push
        grouped by job status.
        """
        try:
            push = Push.objects.get(id=pk)
        except Push.DoesNotExist:
            return Response("No push with id: {0}".format(pk),
                            status=HTTP_404_NOT_FOUND)
        return Response(push.get_status())

    @action(detail=False)
    def health(self, request, project):
        """
        Return a calculated assessment of the health of this push.

        TODO: Replace this static dummy data with real data.
        """
        revision = request.query_params.get('revision')

        try:
            push = Push.objects.get(revision=revision, repository__name=project)
        except Push.DoesNotExist:
            return Response("No push with revision: {0}".format(revision),
                            status=HTTP_404_NOT_FOUND)
        return Response({
            'revision': revision,
            'id': push.id,
            'result': 'fail',
            'metrics': [
                {
                    'name': 'Builds',
                    'result': 'pass',
                    'value': 10,
                    'details': ['Wow, everything passed!'],
                },
                {
                    'name': 'Linting',
                    'result': 'pass',
                    'value': 10,
                    'details': ['Gosh, this code is really nicely formatted.'],
                },
                {
                    'name': 'Tests',
                    'result': 'fail',
                    'value': 2,
                    'failures': [
                        {
                            'testName': 'dom/tests/mochitest/fetch/test_fetch_cors_sw_reroute.html',
                            'jobName': 'test-linux32/opt-mochitest-browser-chrome-e10s-4',
                            'jobId': 223458405,
                            'classification': 'intermittent',
                            'failureLine':
                                'REFTEST TEST-UNEXPECTED-FAIL | file:///builds/worker/workspace/build/tests/reftest/tests/layout/reftests/border-dotted/border-dashed-no-radius.html == file:///builds/worker/workspace/build/tests/reftest/tests/layout/reftests/border-dotted/masked.html | image comparison, max difference: 255, number of differing pixels: 54468',
                            'confidence': 3,
                        },
                        {
                            'testName':
                                'browser/components/extensions/test/browser/test-oop-extensions/browser_ext_pageAction_context.js',
                            'jobName': 'test-linux64/debug-mochitest-plain-headless-e10s-8',
                            'jobId': 223458405,
                            'classification': 'intermittent',
                            'failureLine':
                                "raptor-main TEST-UNEXPECTED-FAIL: test 'raptor-tp6-bing-firefox' timed out loading test page: https://www.bing.com/search?q=barack+obama",
                            'confidence': 4,
                        },
                    ],
                    'details': [
                        'Ran some tests that did not go so well',
                        'See [foo.bar.baz/mongo/rational/fee]',
                    ],
                },
                {
                    'name': 'Coverage',
                    'result': 'indeterminate',
                    'value': 5,
                    'details': [
                        'Covered 42% of the tests that are needed for feature ``foo``.',
                        'Covered 100% of the tests that are needed for feature ``bar``.',
                        'The ratio of people to cake is too many...',
                    ],
                },
                {
                    'name': 'Performance',
                    'result': 'pass',
                    'value': 10,
                    'details': ['Ludicrous Speed'],
                },
            ],
        })
