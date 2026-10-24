#
# This file is part of gunicorn released under the MIT license.
# See the NOTICE for more information.

import datetime
import logging
import logging.handlers
from types import SimpleNamespace

import pytest

from gunicorn.config import Config
from gunicorn.glogging import Logger


def test_atoms_defaults():
    response = SimpleNamespace(
        status='200', response_length=1024,
        headers=(('Content-Type', 'application/json'),), sent=1024,
    )
    request = SimpleNamespace(headers=(('Accept', 'application/json'),))
    environ = {
        'REQUEST_METHOD': 'GET', 'RAW_URI': '/my/path?foo=bar',
        'PATH_INFO': '/my/path', 'QUERY_STRING': 'foo=bar',
        'SERVER_PROTOCOL': 'HTTP/1.1',
    }
    logger = Logger(Config())
    atoms = logger.atoms(response, request, environ, datetime.timedelta(seconds=1))
    assert isinstance(atoms, dict)
    assert atoms['r'] == 'GET /my/path?foo=bar HTTP/1.1'
    assert atoms['m'] == 'GET'
    assert atoms['U'] == '/my/path'
    assert atoms['q'] == 'foo=bar'
    assert atoms['H'] == 'HTTP/1.1'
    assert atoms['b'] == '1024'
    assert atoms['B'] == 1024
    assert atoms['{accept}i'] == 'application/json'
    assert atoms['{content-type}o'] == 'application/json'


def test_atoms_zero_bytes():
    response = SimpleNamespace(
        status='200', response_length=0,
        headers=(('Content-Type', 'application/json'),), sent=0,
    )
    request = SimpleNamespace(headers=(('Accept', 'application/json'),))
    environ = {
        'REQUEST_METHOD': 'GET', 'RAW_URI': '/my/path?foo=bar',
        'PATH_INFO': '/my/path', 'QUERY_STRING': 'foo=bar',
        'SERVER_PROTOCOL': 'HTTP/1.1',
    }
    logger = Logger(Config())
    atoms = logger.atoms(response, request, environ, datetime.timedelta(seconds=1))
    assert atoms['b'] == '0'
    assert atoms['B'] == 0


@pytest.mark.parametrize('auth', [
    # auth type is case in-sensitive
    'Basic YnJrMHY6',
    'basic YnJrMHY6',
    'BASIC YnJrMHY6',
])
def test_get_username_from_basic_auth_header(auth):
    request = SimpleNamespace(headers=())
    response = SimpleNamespace(
        status='200', response_length=1024, sent=1024,
        headers=(('Content-Type', 'text/plain'),),
    )
    environ = {
        'REQUEST_METHOD': 'GET', 'RAW_URI': '/my/path?foo=bar',
        'PATH_INFO': '/my/path', 'QUERY_STRING': 'foo=bar',
        'SERVER_PROTOCOL': 'HTTP/1.1',
        'HTTP_AUTHORIZATION': auth,
    }
    logger = Logger(Config())
    atoms = logger.atoms(response, request, environ, datetime.timedelta(seconds=1))
    assert atoms['u'] == 'brk0v'


def test_get_username_handles_malformed_basic_auth_header():
    """Should catch a malformed auth header"""
    request = SimpleNamespace(headers=())
    response = SimpleNamespace(
        status='200', response_length=1024, sent=1024,
        headers=(('Content-Type', 'text/plain'),),
    )
    environ = {
        'REQUEST_METHOD': 'GET', 'RAW_URI': '/my/path?foo=bar',
        'PATH_INFO': '/my/path', 'QUERY_STRING': 'foo=bar',
        'SERVER_PROTOCOL': 'HTTP/1.1',
        'HTTP_AUTHORIZATION': 'Basic ixsTtkKzIpVTncfQjbBcnoRNoDfbnaXG',
    }
    logger = Logger(Config())

    atoms = logger.atoms(response, request, environ, datetime.timedelta(seconds=1))
    assert atoms['u'] == '-'


def _logconfig(path, fmt):
    return {
        'version': 1,
        'disable_existing_loggers': False,
        'root': {'level': 'WARNING', 'handlers': ['console']},
        'formatters': {'fmt': {'format': fmt}},
        'handlers': {
            'console': {
                'class': 'logging.StreamHandler',
                'formatter': 'fmt',
                'stream': 'ext://sys.stderr',
            },
            'error_file': {
                'class': 'logging.FileHandler',
                'filename': str(path),
                'formatter': 'fmt',
            },
        },
        'loggers': {
            'gunicorn.error': {
                'handlers': ['error_file'],
                'level': 'DEBUG',
                'propagate': False,
            },
        },
    }


def test_setup_reapplies_loglevel():
    """Re-running setup picks up loglevel changes (#3353)."""
    cfg = Config()
    logger = Logger(cfg)
    assert logger.loglevel == logging.INFO

    cfg.set('loglevel', 'debug')
    logger.setup(cfg)

    assert logger.loglevel == logging.DEBUG
    assert logger.error_log.level == logging.DEBUG


def test_setup_reapplies_logconfig_dict(tmp_path):
    """Re-running setup re-reads logconfig_dict, switching handlers (#3353)."""
    cfg = Config()
    cfg.set('logconfig_dict', _logconfig(tmp_path / 'v1.log', '[V1] %(message)s'))
    logger = Logger(cfg)
    try:
        cfg.set('logconfig_dict',
                _logconfig(tmp_path / 'v2.log', '[V2] %(message)s'))
        logger.setup(cfg)

        logger.error('after reload')

        v1_log = tmp_path / 'v1.log'
        v1 = v1_log.read_text() if v1_log.exists() else ''
        assert 'after reload' not in v1
        assert '[V2] after reload' in (tmp_path / 'v2.log').read_text()
    finally:
        # don't leak the dictConfig handlers into other tests
        for handler in list(logger.error_log.handlers):
            logger.error_log.removeHandler(handler)


def test_setup_twice_does_not_stack_handlers(tmp_path):
    """Re-running setup replaces our handlers instead of stacking them."""
    cfg = Config()
    cfg.set('errorlog', str(tmp_path / 'error.log'))
    logger = Logger(cfg)

    logger.setup(cfg)

    gunicorn_handlers = [
        h for h in logger.error_log.handlers
        if getattr(h, '_gunicorn', False)
    ]
    assert len(gunicorn_handlers) == 1


def test_setup_twice_does_not_stack_syslog_handlers():
    """Re-running setup with syslog enabled must not duplicate handlers."""
    cfg = Config()
    cfg.set('syslog', True)
    cfg.set('syslog_addr', 'udp://127.0.0.1:514')
    logger = Logger(cfg)
    try:
        logger.setup(cfg)

        for log in (logger.error_log, logger.access_log):
            syslog_handlers = [
                h for h in log.handlers
                if isinstance(h, logging.handlers.SysLogHandler)
            ]
            assert len(syslog_handlers) == 1
    finally:
        # don't leak the syslog handlers into other tests
        for log in (logger.error_log, logger.access_log):
            for handler in list(log.handlers):
                log.removeHandler(handler)


def _gunicorn_error_handlers(logger):
    return [
        h for h in logger.error_log.handlers
        if getattr(h, '_gunicorn', False)
    ]


def test_error_log_format_default_matches_builtin():
    """Without error_log_format, the error handler keeps the old format."""
    cfg = Config()
    assert cfg.error_log_format is None
    logger = Logger(cfg)
    try:
        handlers = _gunicorn_error_handlers(logger)
        assert len(handlers) == 1
        formatter = handlers[0].formatter
        assert formatter._fmt == Logger.error_fmt
        record = logging.LogRecord(
            'gunicorn.error', logging.INFO, __file__, 1,
            'hello', (), None,
        )
        assert formatter.format(record).endswith('[INFO] hello')
    finally:
        for handler in list(logger.error_log.handlers):
            logger.error_log.removeHandler(handler)


def test_error_log_format_is_applied(tmp_path):
    """A custom error_log_format controls error log output."""
    cfg = Config()
    cfg.set('errorlog', str(tmp_path / 'error.log'))
    cfg.set('error_log_format', 'ERR|%(levelname)s|%(message)s')
    logger = Logger(cfg)
    try:
        logger.error('something broke')
        for handler in logger.error_log.handlers:
            handler.flush()
        content = (tmp_path / 'error.log').read_text()
        assert content == 'ERR|ERROR|something broke\n'
    finally:
        for handler in list(logger.error_log.handlers):
            logger.error_log.removeHandler(handler)


def test_error_log_format_reapplied_on_setup(tmp_path):
    """Re-running setup picks up a changed error_log_format."""
    cfg = Config()
    cfg.set('errorlog', str(tmp_path / 'error.log'))
    cfg.set('error_log_format', 'ONE %(message)s')
    logger = Logger(cfg)
    try:
        cfg.set('error_log_format', 'TWO %(message)s')
        logger.setup(cfg)

        logger.error('after reload')
        for handler in logger.error_log.handlers:
            handler.flush()
        content = (tmp_path / 'error.log').read_text()
        assert 'TWO after reload' in content
        assert 'ONE' not in content
    finally:
        for handler in list(logger.error_log.handlers):
            logger.error_log.removeHandler(handler)


def test_error_log_format_does_not_change_access_format(tmp_path):
    """error_log_format must not leak into the access log format."""
    cfg = Config()
    cfg.set('errorlog', str(tmp_path / 'error.log'))
    cfg.set('error_log_format', 'ERR %(message)s')
    logger = Logger(cfg)
    try:
        handlers = _gunicorn_error_handlers(logger)
        assert handlers[0].formatter._fmt == 'ERR %(message)s'
        access_handlers = [
            h for h in logger.access_log.handlers
            if getattr(h, '_gunicorn', False)
        ]
        for handler in access_handlers:
            assert handler.formatter._fmt == Logger.access_fmt
    finally:
        for log in (logger.error_log, logger.access_log):
            for handler in list(log.handlers):
                log.removeHandler(handler)
