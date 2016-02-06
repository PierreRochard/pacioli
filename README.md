git clone https://github.com/PierreRochard/pacioli

git clone https://github.com/mattupstate/flask-security

git clone https://github.com/PierreRochard/ofxtools

pip install -r pacioli/instance-requirements.txt


cd ofxtools/

python setup.py install

cd ..

cd flask-security/

git checkout develop

python setup.py install

python manage.py createdb

python manage.py create_admin -e you@gmail.com -p password

python manage.py populate_chart_of_accounts

python manage.py populate_chart_of_accounts

python manage.py db init

python manage.py db migrate

python manage.py db upgrade



