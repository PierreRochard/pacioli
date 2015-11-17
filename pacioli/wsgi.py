import os
import sys
import logging

from pacioli import create_app

logging.basicConfig(stream=sys.stderr)

env = os.environ.get('pacioli_ENV', 'dev')
app = create_app('pacioli.settings.%sConfig' % env.capitalize(), env=env)
