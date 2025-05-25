.PHONY: start-db stop-db dev migrate shell reset-db

start-db:
	docker-compose up -d mysql

stop-db:
	docker-compose down

dev: start-db
	sleep 5
	python manage.py runserver

migrate:
	python manage.py migrate

shell:
	python manage.py shell

reset-db:
	docker-compose down -v
	docker-compose up -d mysql
	sleep 15
	python manage.py migrate
	python manage.py createsuperuser