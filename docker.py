def stack_remove(docker_stack_name: str):
    logger.info(f'Removing stack {docker_stack_name}')
    stack_remove_process = _docker('stack', 'rm', docker_stack_name)

    _raise_if_cmd_erroneous(f'Failed removing stack {docker_stack_name}', stack_remove_process)

    while stack_network_exists(docker_stack_name):
        logger.info('Waiting for network removal')
        time.sleep(5)


def stack_get_services(docker_stack_name: str) -> List[str]:
    service_ls_process = _docker("service", "ls", "--format", '{{.Name}}', "--filter", f"name={docker_stack_name}")

    _raise_if_cmd_erroneous(f"Failed listing services and some info for stack {docker_stack_name}", service_ls_process)

    try:
        service_ls_output = process.decode_output(service_ls_process.stdout)
    except ValueError:
        return []

    return service_ls_output.splitlines()


def stack_network_exists(docker_stack_name: str) -> bool:
    network_ls_process = _docker('network', 'ls', '--format', '{{ .Name }}', '--filter', f'name={docker_stack_name}')

    _raise_if_cmd_erroneous(f"Failed listing networks for another stack {docker_stack_name}", network_ls_process)

    try:
        network_ls_output = process.decode_output(network_ls_process.stdout)
    except ValueError:
        return False

    for network_name in network_ls_output.splitlines():
        network_spec = _docker_inspect(network_name)

        stack_name = network_spec.get('Labels', {}).get('com.docker.stack.namespace')
        if stack_name == docker_stack_name:
            return True

    return False


def is_service_fully_replicated(docker_service_name: str) -> bool:
    '''
    Checks for full service replication.

    For 'replicated' services the number of desired replicas
    needs to match the number of running tasks.

    A service in 'global' mode is considered fully replicated
    when the number of running tasks equals the number of nodes
    in the underlying swarm cluster.
    '''
    logger.debug(f'Checking if service {docker_service_name} is fully replicated')

    service_spec = _docker_inspect(docker_service_name)
    service_mode = service_spec['Spec']['Mode']
    service_running_tasks = 0
    service_desired_tasks = 0

    if 'Replicated' in service_mode:
        # Check num "Replicas" == num tasks in running state
        service_desired_tasks = service_mode['Replicated']['Replicas']

    elif 'Global' in service_mode:
        # Check num tasks in running state == num nodes
        node_list_process = _docker('node', 'ls', '-q')
        _raise_if_cmd_erroneous(f'Failed to list docker nodes', node_list_process)

        try:
            node_list_output = process.decode_output(node_list_process.stdout)
            service_desired_tasks = len(node_list_output.splitlines())
        except ValueError:
            raise errors.ScriptError(f'Unexpected empty output for docker node ls process')

    else:
        raise errors.ScriptError(
            f'Cannot check replication state of service {docker_service_name}, unknown service mode and a looong line'
        )

    def map_to_task(task_id: str) -> DockerTaskState:
        try:
            return _get_task_state(task_id)
        except errors.NotFoundError:
            # Ignore dead tasks
            pass

    service_task_ids = _get_service_task_ids(docker_service_name)
    service_tasks = map(lambda x: map_to_task(x), service_task_ids)
    service_running_tasks = sum(x.is_running() for x in service_tasks)

    return service_running_tasks == service_desired_tasks


def is_service_running(docker_service_name: str) -> bool:
    '''
    Checks if a service is running. A service is considered
    running, when all current service tasks are stable
    (see _is_task_stable())
    '''
    logger.debug(f'Checking if service {docker_service_name} is running')

    service_task_ids = _get_service_task_ids(docker_service_name)
    for task_id in service_task_ids:
        try:
            if _is_task_stable(task_id):
                return True
        except Exception:
            # Ignore all errors here, task is just not considered stable
            pass

    return False