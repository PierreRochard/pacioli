from __future__ import print_function
import os
import shutil
import subprocess
import sys
import uuid


python = sys.executable


def run_command(command, cwd=None):
    print(subprocess.Popen(command, stdout=subprocess.PIPE,
                           cwd=cwd).stdout.read())


def install_dependencies():
    library_directory = os.path.join(os.path.dirname(__file__), 'libraries')
    if not os.path.exists(library_directory):
        os.mkdir(library_directory)

    run_command(['git', 'pull'])
    run_command(['pip', '--no-cache-dir', 'install', '--upgrade', '-r',
                 'instance-requirements.txt'])

    custom_repos = [{'package': 'ofxtools',
                     'url': 'https://github.com/PierreRochard/ofxtools',
                     'branch': None},
                    ]
    for repo in custom_repos:
        directory = os.path.join(library_directory, repo['package'])
        if not os.path.exists(directory):
            run_command(['git', 'clone', repo['url'], directory])
            if repo['branch']:
                run_command(['git', 'checkout', repo['branch']],
                            cwd=directory)
        else:
            if repo['branch']:
                run_command(['git', 'checkout', repo['branch']],
                            cwd=directory)
            run_command(['git', 'pull'], cwd=directory)
        run_command([python, 'setup.py', 'install'], cwd=directory)


def setup_settings():
    pacioli_directory = os.path.join(os.path.dirname(__file__), 'pacioli')
    settings_file = os.path.join(pacioli_directory, 'settings.py')
    example_settings_file = os.path.join(pacioli_directory, 'settings_example.py')
    if not os.path.exists(settings_file):
        shutil.copy(example_settings_file, settings_file)
        print('Update pacioli/settings.py and then run this script again.')
        sys.exit(0)


def setup_database():
    run_command([python, 'manage.py', 'createdb'])
    run_command([python, 'manage.py', 'populate_chart_of_accounts'])
    admin_password = str(uuid.uuid4()).replace('-', '')
    run_command([python, 'manage.py', 'create_admin', '-e', 'admin@localhost',
                 '-p', admin_password])
    print('admin@localhost password: {0}'.format(admin_password))


def setup_cron():
    directory = os.path.dirname(os.path.abspath(__file__))
    cron_job = '30 11,23 * * * cd {0} && python manage.py ' \
               'update_ofx'.format(directory)
    with open('mycron', 'w') as cron_file:
        cron_file.write(cron_job)
    run_command(['crontab', 'mycron'])
    os.remove('mycron')

if __name__ == '__main__':
    install_dependencies()
    # setup_settings()
    # setup_database()
    # setup_cron()
