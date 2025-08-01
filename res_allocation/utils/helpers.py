def create_app_to_vm_map(vm_to_app_map):
    """
    Creates a mapping from app/user IDs to VM IDs for quick lookups.
    
    Args:
        vm_to_app_map (dict): A dictionary mapping VM IDs to lists of app IDs
        
    Returns:
        dict: A dictionary mapping app IDs to VM IDs
    """
    app_to_vm_map = {}
    for vm, apps in vm_to_app_map.items():
        for app in apps:
            app_to_vm_map[app] = vm
    return app_to_vm_map