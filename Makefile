.PHONY: install up down migrate run worker beat test lint

install:
	pip install -r requirements.txt -r requirements-dev.txt

up:
	cd .. && docker compose up -d

down:
	cd .. && docker compose down

migrate:
	python manage.py migrate

run:
	python manage.py runserver

worker:
	celery -A config worker -l info --pool=solo

beat:
	celery -A config beat -l info

test:
	pytest

lint:
	ruff check .
