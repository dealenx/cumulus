from __future__ import absolute_import
import traceback
from cumulus.starcluster.logging import logstdout
import cumulus.starcluster.logging
from cumulus.common import check_status
from cumulus.starcluster.common import _log_exception, get_ssh_connection
from cumulus.celery import command, monitor
import cumulus
import cumulus.girderclient
from cumulus.constants import ClusterType
from cumulus.queue import get_queue_adapter
import starcluster.config
import starcluster.logger
import starcluster.exception
import requests
import tempfile
import os
import re
import inspect
import time
from celery import signature
import StringIO
from jinja2 import Template
from jsonpath_rw import parse


def _put_script(ssh, script_commands):
    with tempfile.NamedTemporaryFile() as script:
        script_name = os.path.basename(script.name)
        script.write(script_commands)
        script.write('echo $!\n')
        script.flush()
        ssh.put(script.name)

        cmd = './%s' % script_name
        ssh.execute('chmod 700 %s' % cmd)

    return cmd


def _job_dir(job):
    job_dir = './%s' % job['_id']
    output_root = parse('params.jobOutputDir').find(job)

    if output_root:
        output_root = output_root[0].value
        job_dir = os.path.join(output_root, job['_id'])

    return job_dir


@command.task
@cumulus.starcluster.logging.capture
def download_job_input(cluster, job, log_write_url=None, girder_token=None):
    log = starcluster.logger.get_starcluster_logger()
    headers = {'Girder-Token':  girder_token}
    job_id = job['_id']
    status_url = '%s/jobs/%s' % (cumulus.config.girder.baseUrl, job_id)
    job_name = job['name']

    try:
        with get_ssh_connection(girder_token, cluster) as ssh:
            # First put girder client on master
            path = inspect.getsourcefile(cumulus.girderclient)
            ssh.put(path)

            # Create job directory
            ssh.mkdir(_job_dir(job))

            log.info('Downloading input for "%s"' % job_name)

            r = requests.patch(status_url, json={'status': 'downloading'},
                               headers=headers)
            check_status(r)

            download_cmd = 'python girderclient.py --token %s --url "%s" ' \
                           'download --dir %s  --job %s' \
                % (girder_token, cumulus.config.girder.baseUrl,
                   _job_dir(job), job_id)

            download_output = '%s.download.out' % job_id
            download_cmd = 'nohup %s  &> %s  &\n' % (download_cmd,
                                                     download_output)

            download_cmd = _put_script(ssh, download_cmd)
            output = ssh.execute(download_cmd)

            # Remove download script
            ssh.unlink(download_cmd)

        if len(output) != 1:
            raise Exception('PID not returned by execute command')

        try:
            pid = int(output[0])
        except ValueError:
            raise Exception('Unable to extract PID from: %s' % output)

        # When the download is complete submit the job
        on_complete = submit_job.s(cluster, job, log_write_url=log_write_url,
                                   girder_token=girder_token)

        monitor_process.delay(cluster, job, pid, download_output,
                              log_write_url=log_write_url,
                              on_complete=on_complete,
                              girder_token=girder_token)

    except Exception as ex:
        r = requests.patch(status_url, headers=headers,
                           json={'status': 'error'})
        check_status(r)
        _log_exception(ex)


def _get_parallel_env(cluster, job):
    parallel_env = None
    if 'parallelEnvironment' in job.get('params', {}):
        parallel_env = job['params']['parallelEnvironment']
    elif 'parallelEnvironment' in cluster['config']:
        parallel_env = cluster['config']['parallelEnvironment']

    # if this is a ec2 cluster then we can default to orte
    if not parallel_env and cluster['type'] == ClusterType.EC2:
        parallel_env = 'orte'

    return parallel_env


def _get_number_of_slots(ssh, parallel_env):
    slots = -1
    # First get number of slots available
    output = ssh.execute('qconf -sp %s' % parallel_env)

    for line in output:
        m = re.match('slots[\s]+(\d+)', line)
        if m:
            slots = m.group(1)
            break

    if slots < 1:
        raise Exception('Unable to retrieve number of slots')

    return slots


def _is_terminating(job, girder_token):
    headers = {'Girder-Token':  girder_token}
    status_url = '%s/jobs/%s/status' % (cumulus.config.girder.baseUrl,
                                        job['_id'])
    r = requests.get(status_url, headers=headers)
    check_status(r)
    current_status = r.json()['status']

    return current_status in ['terminated', 'terminating']


def _generate_script_template(job):
    script_template = StringIO.StringIO()
    for c in job['commands']:
        script_template.write('%s\n' % c)

    return script_template


@command.task
@cumulus.starcluster.logging.capture
def submit_job(cluster, job, log_write_url=None, girder_token=None):

    log = starcluster.logger.get_starcluster_logger()
    script_filepath = None
    headers = {'Girder-Token':  girder_token}
    job_id = job['_id']
    job_dir = _job_dir(job)
    status_url = '%s/jobs/%s' % (cumulus.config.girder.baseUrl, job_id)
    try:
        # if terminating break out
        if _is_terminating(job, girder_token):
            return

        # Write out script to upload to master
        (_, script_filepath) = tempfile.mkstemp()
        script_name = job['name']
        script_filepath = os.path.join(tempfile.gettempdir(), script_name)

        script_template = _generate_script_template(job)

        with logstdout():
            with get_ssh_connection(girder_token, cluster) as ssh:
                job_params = {}
                if 'params' in job:
                    job_params = job['params']

                parallel_env = _get_parallel_env(cluster, job)
                if parallel_env:
                    job_params['parallelEnvironment'] = parallel_env

                slots = -1
                # If the number of slots has not been provided we will get the
                # number of slots from the parallel environment
                if ('numberOfSlots' not in cluster['config']) and parallel_env:
                    slots = _get_number_of_slots(ssh, parallel_env)
                    if slots > 0:
                        job_params['numberOfSlots'] = int(slots)

                # Now we can template submission script
                script = Template(script_template.getvalue()) \
                    .render(cluster=cluster,
                            job=job, baseUrl=cumulus.config.girder.baseUrl,
                            **job_params)

                with open(script_filepath, 'w') as fp:
                    fp.write('%s\n' % script)

                ssh.mkdir(job_dir, ignore_failure=True)
                # put the script to master
                ssh.put(script_filepath, job_dir)
                # Now submit the job

                if slots > -1:
                    log.info('We have %s slots available' % slots)

                submit_cmd \
                    = get_queue_adapter(cluster).submit_job_command(script_name)
                cmd = 'cd %s && %s' \
                    % (job_dir, submit_cmd)

                output = ssh.execute(cmd)

            if len(output) != 1:
                raise Exception('Unexpected output: %s' % output)

            queue_adapter = get_queue_adapter(cluster)
            queue_job_id = queue_adapter.parse_job_id(output)

            # Update the state and sge id

            r = requests.patch(status_url, headers=headers,
                               json={'status': 'queued',
                                     queue_adapter.QUEUE_JOB_ID: queue_job_id})
            check_status(r)
            job = r.json()

            job['queuedTime'] = time.time()

            # Now monitor the jobs progress
            monitor_job.s(cluster, job, log_write_url=log_write_url,
                          girder_token=girder_token).apply_async(countdown=5)

        # Now update the status of the cluster
        headers = {'Girder-Token':  girder_token}
        r = requests.patch(status_url, headers=headers,
                           json={'status': 'queued'})
        check_status(r)
    except starcluster.exception.RemoteCommandFailed as ex:
        r = requests.patch(status_url, headers=headers,
                           json={'status': 'error'})
        check_status(r)
        _log_exception(ex)
    except starcluster.exception.ClusterDoesNotExist as ex:
        r = requests.patch(status_url, headers=headers,
                           json={'status': 'error'})
        check_status(r)
        _log_exception(ex)
    except Exception as ex:
        traceback.print_exc()
        r = requests.patch(status_url, headers=headers,
                           json={'status': 'error'})
        check_status(r)
        _log_exception(ex)
        raise
    finally:
        if script_filepath and os.path.exists(script_filepath):
            os.remove(script_filepath)


def submit(girder_token, cluster, job, log_url):
    # Do we inputs to download ?
    if 'input' in job and len(job['input']) > 0:

        download_job_input.delay(cluster, job, log_write_url=log_url,
                                 girder_token=girder_token)
    else:
        submit_job.delay(cluster, job, log_write_url=log_url,
                         girder_token=girder_token)


def _tail_output(job, ssh):
    log = starcluster.logger.get_starcluster_logger()

    output_updated = False

    # Do we need to tail any output files
    for output in job.get('output', []):
        if 'tail' in output and output['tail']:
            path = output['path']
            offset = 0
            if 'content' in output:
                offset = len(output['content'])
            else:
                output['content'] = []
            tail_path = os.path.join(_job_dir(job), path)
            command = 'tail -n +%d %s' % (offset, tail_path)
            try:
                # Only tail if file exists
                if ssh.isfile(tail_path):
                    stdout = ssh.execute(command)
                    output['content'] = output['content'] + stdout
                    output_updated = True
                else:
                    log.info('Skipping tail of %s as file doesn\'t '
                             'currently exist' %
                             tail_path)
            except starcluster.exception.RemoteCommandFailed as ex:
                _log_exception(ex)

    return output_updated


def _job_state(ssh, cluster, job):
    queue_adapter = get_queue_adapter(cluster)
    status_command = queue_adapter.job_status_command(job)
    output = ssh.execute(status_command)

    return queue_adapter.extract_job_status(output, job)


def _handle_queued_or_running(task, cluster, job, state):
    timings = {}
    queue_adapter = get_queue_adapter(cluster)

    if queue_adapter.is_running(state):
        status = 'running'
        if 'queuedTime' in job:
            queued_time = time.time() - job['queuedTime']
            timings = {'queued': int(round(queued_time * 1000))}
            del job['queuedTime']
            job['runningTime'] = time.time()
    elif queue_adapter.is_queued(state):
        status = 'queued'
    else:
        raise Exception('Unrecognized SGE state: %s' % state)

    # Job is still active so schedule self again in about 5 secs
    # N.B. throw=False to prevent Retry exception being raised
    task.retry(throw=False, countdown=5)

    return status, timings


def _handle_complete(ssh, cluster, job, log_write_url, girder_token, status):
    log = starcluster.logger.get_starcluster_logger()
    job_name = job['name']
    job_dir = _job_dir(job)
    timings = {}

    if 'runningTime' in job:
        running_time = time.time() - job['runningTime']
        timings = {'running': int(round(running_time * 1000))}
        del job['runningTime']

    # See if we have anything in stderr, if so we will mark the job as errored
    # The exception is the pvw job as it writes stuff to stderr during normal
    # operation. This is a horrible special case and should be fixed.
    if job_name != 'pvw':
        try:
            stderr_filename = '%s.e%s' % (job['name'], job['queueJobId'])
            stderr_path = os.path.join(job_dir, stderr_filename)
            stat_attrs = ssh.stat(stderr_path)
            print stat_attrs.st_size
            if stat_attrs.st_size > 0:
                status = 'error'
        except IOError:
            pass

    # Fire off task to upload the output
    log.info('Job "%s" complete' % job_name)
    if 'output' in job and len(job['output']) > 0:
        if status == 'error':
            status = 'error_uploading'
        else:
            status = 'uploading'
        job['status'] = status
        upload_job_output.delay(cluster, job, log_write_url=log_write_url,
                                job_dir=job_dir, girder_token=girder_token)
    elif _get_on_complete(job) == 'terminate':
        cluster_log_url = '%s/clusters/%s/log' % \
            (cumulus.config.girder.baseUrl, cluster['_id'])
        command.send_task('cumulus.starcluster.tasks.cluster.terminate_cluster',
                          args=(cluster,),
                          kwargs={'log_write_url': cluster_log_url,
                                  'girder_token': girder_token})

    return status, timings


def _get_on_complete(job):
    on_complete = parse('onComplete.cluster').find(job)

    if on_complete:
        on_complete = on_complete[0].value
    else:
        on_complete = None

    return on_complete


@monitor.task(bind=True, max_retries=None)
@cumulus.starcluster.logging.capture
def monitor_job(task, cluster, job, log_write_url=None, girder_token=None):
    headers = {'Girder-Token':  girder_token}
    job_id = job['_id']

    status_update_url = '%s/jobs/%s' % (cumulus.config.girder.baseUrl, job_id)
    status_url = '%s/jobs/%s/status' % (cumulus.config.girder.baseUrl, job_id)

    try:
        with get_ssh_connection(girder_token, cluster) as ssh:
            # First get the current status
            r = requests.get(status_url, headers=headers)
            check_status(r)

            current_status = r.json()['status']

            if current_status == 'terminated':
                return

            try:
                state = _job_state(ssh, cluster, job)
            except EOFError:
                # Try again
                task.retry(throw=False, countdown=5)
                return
            except starcluster.exception.SSHConnectionError as ex:
                # Try again
                task.retry(throw=False, countdown=5)
                return

            # If not in queue and we are terminating then move to terminated
            if current_status == 'terminating':
                status = 'terminated'
            # Otherwise we are complete
            else:
                status = 'complete'

            timings = {}

            if state and current_status != 'terminating':
                status, timings = _handle_queued_or_running(task, cluster,
                                                            job, state)
            elif status == 'complete':
                status, timings = _handle_complete(ssh, cluster, job,
                                                   log_write_url,
                                                   girder_token, status)

            output_updated = _tail_output(job, ssh)

        json = {
            'status': status,
            'timings': timings
        }

        if output_updated:
            json['output'] = job['output']

        r = requests.patch(status_update_url, headers=headers, json=json)
        check_status(r)
    except starcluster.exception.RemoteCommandFailed as ex:
        r = requests.patch(status_update_url, headers=headers,
                           json={'status': 'error'})
        check_status(r)
        _log_exception(ex)
    except Exception as ex:
        traceback.print_exc()
        r = requests.patch(status_update_url, headers=headers,
                           json={'status': 'error'})
        check_status(r)
        _log_exception(ex)
        raise


@command.task
@cumulus.starcluster.logging.capture
def upload_job_output(cluster, job, log_write_url=None, job_dir=None,
                      girder_token=None):
    log = starcluster.logger.get_starcluster_logger()
    headers = {'Girder-Token':  girder_token}
    job_id = job['_id']
    status_url = '%s/jobs/%s' % (cumulus.config.girder.baseUrl, job_id)
    job_name = job['name']

    try:
        # if terminating break out
        if _is_terminating(job, girder_token):
            return

        with get_ssh_connection(girder_token, cluster) as ssh:
            # First put girder client on master
            path = inspect.getsourcefile(cumulus.girderclient)
            ssh.put(path, os.path.normpath(os.path.join(job_dir, '..')))

            log.info('Uploading output for "%s"' % job_name)

            cmds = ['cd %s' % job_dir]
            upload_cmd = 'python ../girderclient.py --token %s --url "%s" ' \
                         'upload --job %s' \
                         % (girder_token,
                            cumulus.config.girder.baseUrl, job['_id'])

            upload_output = '%s.upload.out' % job_id
            upload_output_path = os.path.normpath(os.path.join(job_dir, '..',
                                                               upload_output))
            cmds.append('nohup %s  &> ../%s  &\n' % (upload_cmd, upload_output))

            upload_cmd = _put_script(ssh, '\n'.join(cmds))
            output = ssh.execute(upload_cmd)

            # Remove upload script
            ssh.unlink(upload_cmd)

        if len(output) != 1:
            raise Exception('PID not returned by execute command')

        try:
            pid = int(output[0])
        except ValueError:
            raise Exception('Unable to extract PID from: %s' % output)

        on_complete = None

        if _get_on_complete(job) == 'terminate':
            cluster_log_url = '%s/clusters/%s/log' % \
                (cumulus.config.girder.baseUrl, cluster['_id'])
            on_complete = signature(
                'cumulus.starcluster.tasks.cluster.terminate_cluster',
                args=(cluster,), kwargs={'log_write_url': cluster_log_url,
                                         'girder_token': girder_token})

        monitor_process.delay(cluster, job, pid, upload_output_path,
                              log_write_url=log_write_url,
                              on_complete=on_complete,
                              girder_token=girder_token)

    except starcluster.exception.RemoteCommandFailed as ex:
        r = requests.patch(status_url, headers=headers,
                           json={'status': 'error'})
        check_status(r)
        _log_exception(ex)


@monitor.task(bind=True, max_retries=None)
@cumulus.starcluster.logging.capture
def monitor_process(task, cluster, job, pid, nohup_out_path,
                    log_write_url=None, on_complete=None,
                    output_message='Job download/upload error: %s',
                    girder_token=None):
    log = starcluster.logger.get_starcluster_logger()
    headers = {'Girder-Token':  girder_token}
    job_id = job['_id']
    status_url = '%s/jobs/%s' % (cumulus.config.girder.baseUrl, job_id)

    try:
        # if terminating break out
        if _is_terminating(job, girder_token):
            return

        with get_ssh_connection(girder_token, cluster) as ssh:
            # See if the process is still running
            output = ssh.execute('ps %s | grep %s' % (pid, pid),
                                 ignore_exit_status=True,
                                 source_profile=False)

            if len(output) > 0:
                # Process is still running so schedule self again in about 5
                # secs
                # N.B. throw=False to prevent Retry exception being raised
                task.retry(throw=False, countdown=5)
            else:
                try:
                    nohup_out_file_name = os.path.basename(nohup_out_path)
                    ssh.get(nohup_out_path)
                    # Log the output
                    with open(nohup_out_file_name, 'r') as fp:
                        output = fp.read()
                        if output.strip():
                            log.error(output_message % output)
                            # If we have output then set the error state on the
                            # job and return
                            r = requests.patch(status_url, headers=headers,
                                               json={'status': 'error'})
                            check_status(r)
                            return
                finally:
                    if nohup_out_file_name and \
                       os.path.exists(nohup_out_file_name):
                        os.remove(nohup_out_file_name)

                # Fire off the on_compete task if we have one
                if on_complete:
                    signature(on_complete).delay()

                # If we where uploading move job into complete state
                if job['status'] == 'uploading':
                    r = requests.patch(status_url, headers=headers,
                                       json={'status': 'complete'})
                    check_status(r)
                elif job['status'] == 'error_uploading':
                    r = requests.patch(status_url, headers=headers,
                                       json={'status': 'error'})
                    check_status(r)

    except EOFError:
        # Try again
        task.retry(throw=False, countdown=5)
    except starcluster.exception.RemoteCommandFailed as ex:
        r = requests.patch(status_url, headers=headers,
                           json={'status': 'error'})
        check_status(r)
        _log_exception(ex)
    except Exception as ex:
        r = requests.patch(status_url, headers=headers,
                           json={'status': 'error'})
        check_status(r)
        _log_exception(ex)
        raise


@command.task
@cumulus.starcluster.logging.capture
def terminate_job(cluster, job, log_write_url=None, girder_token=None):
    script_filepath = None
    headers = {'Girder-Token':  girder_token}
    job_id = job['_id']
    status_url = '%s/jobs/%s' % (cumulus.config.girder.baseUrl, job_id)

    try:

        with logstdout():
            with get_ssh_connection(girder_token, cluster) as ssh:
                queue_adapter = get_queue_adapter(cluster)
                if queue_adapter.QUEUE_JOB_ID in job:
                    terminate_command = queue_adapter.terminate_job_command(job)
                    output = ssh.execute(terminate_command)
                else:
                    r = requests.patch(status_url, headers=headers,
                                       json={'status': 'terminated'})
                    check_status(r)

                if 'onTerminate' in job:
                    commands = '\n'.join(job['onTerminate']['commands']) + '\n'
                    commands = Template(commands) \
                        .render(cluster=cluster,
                                job=job,
                                base_url=cumulus.config.girder.baseUrl)

                    on_terminate = _put_script(ssh, commands + '\n')

                    terminate_output = '%s.terminate.out' % job_id
                    terminate_cmd = 'nohup %s  &> %s  &\n' % (on_terminate,
                                                              terminate_output)
                    terminate_cmd = _put_script(ssh, terminate_cmd)
                    output = ssh.execute(terminate_cmd)

                    ssh.unlink(on_terminate)
                    ssh.unlink(terminate_cmd)

                    if len(output) != 1:
                        raise Exception('PID not returned by execute command')

                    try:
                        pid = int(output[0])
                    except ValueError:
                        raise Exception('Unable to extract PID from: %s'
                                        % output)

                    output_message = 'onTerminate error: %s'
                    monitor_process.delay(cluster, job, pid, terminate_output,
                                          log_write_url=log_write_url,
                                          output_message=output_message,
                                          girder_token=girder_token)

    except starcluster.exception.RemoteCommandFailed as ex:
        r = requests.patch(status_url, headers=headers,
                           json={'status': 'error'})
        check_status(r)
        _log_exception(ex)
    except starcluster.exception.ClusterDoesNotExist as ex:
        r = requests.patch(status_url, headers=headers,
                           json={'status': 'error'})
        check_status(r)
        _log_exception(ex)
    except Exception as ex:
        r = requests.patch(status_url, headers=headers,
                           json={'status': 'error'})
        check_status(r)
        _log_exception(ex)
        raise
    finally:
        if script_filepath and os.path.exists(script_filepath):
            os.remove(script_filepath)


@command.task(bind=True, max_retries=5)
def remove_output(task, cluster, job, girder_token):
    try:
        with get_ssh_connection(girder_token, cluster) as ssh:
            rm_cmd = 'rm -rf %s' % _job_dir(job)
            ssh.execute(rm_cmd)
    except EOFError:
        # Try again
        task.retry(countdown=5)
