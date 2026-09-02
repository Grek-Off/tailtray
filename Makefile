.PHONY: test check run install

test:
	PYTHONPATH=src python3 -m unittest discover -s tests -v

check: test
	python3 -m compileall -q src tests
	@for icon in data/icons/hicolor/scalable/status/*.svg; do xmllint --noout "$$icon"; done
	xmllint --noout data/io.github.tailtray.metainfo.xml
	desktop-file-validate data/io.github.tailtray.desktop

run:
	PYTHONPATH=src python3 -m tailtray.app

install:
	./scripts/install-fedora.sh

