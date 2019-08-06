from .base import *
from dotenv import load_dotenv
import sentry_sdk
from sentry_sdk.integrations.django import DjangoIntegration

load_dotenv()

DEBUG = False
ALLOWED_HOSTS = ["academy.beer"]

SECRET_KEY = os.getenv("SECRET_KEY")

SESSION_COOKIE_SECURE = True

sentry_sdk.init(dsn=os.getenv("SENTRY_DNS"), integrations=[DjangoIntegration()])
