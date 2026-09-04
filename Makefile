.PHONY: test check run install

test:
	PYTHONPATH=src python3 -m unittest discover -s tests -v

check: test
	python3 -m compileall -q src tests
	bash -n scripts/diagnose.sh scripts/install-fedora.sh
	@for icon in data/icons/hicolor/scalable/status/*.svg; do xmllint --noout "$$icon"; done
	@test "$$(find data/tray-icons -maxdepth 1 -name 'tailtray-hc-v2-*.png' | wc -l)" -eq 4
	xmllint --noout data/io.github.tailtray.metainfo.xml
	desktop-file-validate data/io.github.tailtray.desktop

run:
	PYTHONPATH=src python3 -m tailtray.launcher

install:
	./scripts/install-fedora.sh
