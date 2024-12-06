import os
import tarfile
import json


class ProjectManager:
    def __init__(self, repo_path):
        """
        Initialize the ProjectManager with the repository path.

        Args:
            repo_path (str): Path to the repository containing projects.
        """
        self.repo_path = os.path.abspath(repo_path)
        if not os.path.exists(self.repo_path):
            os.makedirs(self.repo_path, exist_ok=True)
            # raise FileNotFoundError(f"Repository path '{self.repo_path}' does not exist.")
        self.projects = {}
        self._parse_projects()
        print(self.projects)

    def _parse_projects(self):
        """
        Parse the repository to load project metadata.
        """
        for item in os.listdir(self.repo_path):
            print(item)
            project_path = os.path.join(self.repo_path, item)
            if os.path.isdir(project_path):
                config_path = os.path.join(project_path, 'config.json')
                if os.path.exists(config_path):
                    with open(config_path, 'r') as config_file:
                        config = json.load(config_file)
                        name = config.get('name')
                        if name:
                            self.projects[name] = {
                                'path': project_path,
                                'config': config,
                                'tar_path': os.path.join(self.repo_path, f"{name}.tar.gz")
                            }

    def tar_project(self, project_name):
        """
        Tar and compress a project directory.

        Args:
            project_name (str): Name of the project to compress.

        Returns:
            str: Path to the created tar file.
        """
        project = self.projects.get(project_name)
        if not project:
            raise ValueError(f"Project '{project_name}' not found in repository.")

        tar_path = project['tar_path']
        project_path = project['path']

        # Create the tarball with the correct structure
        with tarfile.open(tar_path, 'w:gz') as tar:
            # Add the project directory, but adjust arcname to flatten the structure
            for root, dirs, files in os.walk(project_path):
                for file in files:
                    full_path = os.path.join(root, file)
                    # Compute relative path to ensure correct structure
                    relative_path = os.path.relpath(full_path, project_path)
                    tar.add(full_path, arcname=relative_path)

        return tar_path

    def get_tar_location(self, project_name):
        """
        Get the location of the tar file for a project.

        Args:
            project_name (str): Name of the project.

        Returns:
            str: Path to the tar file, if it exists.
        """
        project = self.projects.get(project_name)
        if not project:
            raise ValueError(f"Project '{project_name}' not found in repository.")

        tar_path = project['tar_path']
        if not os.path.exists(tar_path):
            raise FileNotFoundError(f"Tar file for project '{project_name}' does not exist.")

        return tar_path

    def state(self):
        return self.projects
