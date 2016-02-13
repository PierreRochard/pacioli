import os
import shutil
import subprocess
import sys
import uuid


python = sys.executable

library_directory = os.path.join(os.path.dirname(__file__), 'libraries')
if not os.path.exists(library_directory):
    os.mkdir(library_directory)

print subprocess.Popen(['git', 'pull'], stdout=subprocess.PIPE).stdout.read()
print subprocess.Popen(['pip', 'install', '-r', 'instance-requirements.txt'],
                       stdout=subprocess.PIPE).stdout.read()
print subprocess.Popen(['pip', 'install', '--upgrade', '-r', 'instance-requirements.txt'],
                       stdout=subprocess.PIPE).stdout.read()

ofxtools_directory = os.path.join(library_directory, 'ofxtools')
if not os.path.exists(ofxtools_directory):
    print subprocess.Popen(['git', 'clone', 'https://github.com/PierreRochard/ofxtools', ofxtools_directory],
                           stdout=subprocess.PIPE).stdout.read()
else:
    print subprocess.Popen(['git', 'pull'], stdout=subprocess.PIPE, cwd=ofxtools_directory).stdout.read()

flask_security_directory = os.path.join(library_directory, 'flask-security')
if not os.path.exists(flask_security_directory):
    print subprocess.Popen(['git', 'clone', 'https://github.com/PierreRochard/flask-security', flask_security_directory],
                           stdout=subprocess.PIPE).stdout.read()
    print subprocess.Popen(['git', 'checkout', 'develop'], cwd=flask_security_directory,
                           stdout=subprocess.PIPE).stdout.read()
else:
    print subprocess.Popen(['git', 'checkout', 'develop'], cwd=flask_security_directory,
                           stdout=subprocess.PIPE).stdout.read()
    print subprocess.Popen(['git', 'pull'], cwd=flask_security_directory,
                           stdout=subprocess.PIPE).stdout.read()

flask_admin_directory = os.path.join(library_directory, 'flask-admin')
if not os.path.exists(flask_admin_directory):
    print subprocess.Popen(['git', 'clone', 'https://github.com/PierreRochard/flask-admin', flask_admin_directory],
                           stdout=subprocess.PIPE).stdout.read()
else:
    print subprocess.Popen(['git', 'pull'], stdout=subprocess.PIPE, cwd=flask_admin_directory).stdout.read()


print subprocess.Popen([python, 'setup.py', 'install'], stdout=subprocess.PIPE,
                       cwd=ofxtools_directory).stdout.read()
print subprocess.Popen([python, 'setup.py', 'install'], stdout=subprocess.PIPE,
                       cwd=flask_security_directory).stdout.read()
print subprocess.Popen([python, 'setup.py', 'install'], stdout=subprocess.PIPE,
                       cwd=flask_admin_directory).stdout.read()

pacioli_directory = os.path.join(os.path.dirname(__file__), 'pacioli')
settings_file = os.path.join(pacioli_directory, 'settings.py')
example_settings_file = os.path.join(pacioli_directory, 'settings_example.py')
if not os.path.exists(settings_file):
    shutil.copy(example_settings_file, settings_file)
    print 'Update pacioli/settings.py and then run this script again.'
    sys.exit(0)

print subprocess.Popen([python, 'manage.py', 'createdb'], stdout=subprocess.PIPE).stdout.read()

print subprocess.Popen([python, 'manage.py', 'populate_chart_of_accounts'], stdout=subprocess.PIPE).stdout.read()

admin_password = str(uuid.uuid4()).replace('-', '')
print subprocess.Popen([python, 'manage.py', 'create_admin', '-e', 'admin@localhost', '-p', admin_password],
                       stdout=subprocess.PIPE).stdout.read()

print 'admin@localhost password: {0}'.format(admin_password)

with open('mycron', 'w') as cron_file:
    cron_file.write('30 11,23 * * * cd {0} && python manage.py update_ofx'.format(
            os.path.dirname(os.path.abspath(__file__))))

print subprocess.Popen(['crontab', 'mycron'], stdout=subprocess.PIPE).stdout.read()

os.remove('mycron')
