import json
from threading import Lock
import urllib
import webapp2
import copy
import math

import jinja2

from raco import RACompiler
from raco.myrial.exceptions import MyrialCompileException
from raco.myrial import parser as MyrialParser
from raco.myrial import interpreter as MyrialInterpreter
from raco.language import MyriaAlgebra
from raco.myrialang import compile_to_json
from raco.viz import get_dot
from raco import scheme
from examples import examples
from pagination import Pagination

import myria

defaultquery = """A(x) :- R(x,3)"""
hostname = "vega.cs.washington.edu"
port = 1776

# Global connection to Myria. Thread-safe
connection = myria.MyriaConnection(hostname=hostname, port=port)


# We need a (global) lock on the Myrial parser because yacc is not Threadsafe.
# .. see uwescience/datalogcompiler#39
# ..    (https://github.com/uwescience/datalogcompiler/issues/39)
myrial_parser_lock = Lock()
myrial_parser = MyrialParser.Parser()


JINJA_ENVIRONMENT = jinja2.Environment(
    loader=jinja2.FileSystemLoader('templates'),
    extensions=['jinja2.ext.autoescape'],
    autoescape=True)

QUERIES_PER_PAGE = 10


def get_plan(query, language, plan_type, connection_=None):
    # Fix up the language string
    if language is None:
        language = "datalog"
    language = language.strip().lower()

    if language == "datalog":
        dlog = RACompiler()
        dlog.fromDatalog(query)
        if not dlog.logicalplan:
            raise SyntaxError("Unable to parse Datalog")
        if plan_type == 'logical':
            return dlog.logicalplan
        dlog.optimize(target=MyriaAlgebra, eliminate_common_subexpressions=False)
        if plan_type == 'physical':
            return dlog.physicalplan
        else:
            raise NotImplementedError('Datalog plan type %s' % plan_type)
    elif language in ["myrial", "sql"]:
        # We need a (global) lock on the Myrial parser because yacc is not Threadsafe.
        # .. and App Engine uses multiple threads.
        with myrial_parser_lock:
            parsed = myrial_parser.parse(query)
        conn = connection_ or connection
        processor = MyrialInterpreter.StatementProcessor(MyriaCatalog(conn))
        processor.evaluate(parsed)
        if plan_type == 'logical':
            return processor.get_logical_plan()
        elif plan_type == 'physical':
            return processor.get_physical_plan()
        else:
            raise NotImplementedError('Myria plan type %s' % plan_type)
    else:
        raise NotImplementedError('Language %s is not supported' % language)

    raise NotImplementedError('Should not be able to get here')


def get_logical_plan(query, language):
    return get_plan(query, language, 'logical')


def get_physical_plan(query, language=None):
    return get_plan(query, language, 'physical')


def format_rule(expressions):
    if isinstance(expressions, list):
        return "\n".join(["%s = %s" % e for e in expressions])
    return str(expressions)


def get_datasets(connection_=None):
    conn = connection_ or connection
    if not conn:
        return []
    try:
        return conn.datasets()
    except myria.MyriaError:
        return []


class MyriaCatalog:
    def __init__(self, connection_=None):
        self.connection = connection_ or connection

    def get_scheme(self, rel_key):
        relation_args = {
            'userName': rel_key.user,
            'programName': rel_key.program,
            'relationName': rel_key.relation
        }
        if not self.connection:
            raise ValueError("no schema for relation %s because no connection" % rel_key)
        try:
            dataset_info = self.connection.dataset(relation_args)
        except myria.MyriaError:
            raise ValueError(rel_key)
        schema = dataset_info['schema']
        return scheme.Scheme(zip(schema['columnNames'], schema['columnTypes']))


class MyriaHandler(webapp2.RequestHandler):
    def handle_exception(self, exception, debug_mode):
        self.response.headers['Content-Type'] = 'text/plain'
        if isinstance(exception, (ValueError, SyntaxError, MyrialCompileException)):
            self.response.status = 400
            msg = str(exception)
        else:
            self.response.status = 500
            self.response.out.write("Error 500 (Internal Server Error)")
            if debug_mode:
                self.response.out.write(": \n\n")
                import traceback
                msg = traceback.format_exc()

        self.response.out.write(msg)


class RedirectToEditor(MyriaHandler):
    def get(self, query=None):
        if query is not None:
            self.redirect("/editor?query=%s" % urllib.quote(query, ''), True)
        else:
            self.redirect("/editor", True)


class MyriaPage(MyriaHandler):
    def get_connection_string(self, connection_=None):
        conn = connection_ or connection
        if not conn:
            connection_string = "unable to connect to %s:%d" % (hostname, port)
        else:
            try:
                workers = conn.workers()
                alive = conn.workers_alive()
                connection_string = "%s:%d [%d/%d]" % (hostname, port, len(alive), len(workers))
            except:
                connection_string = "error connecting to %s:%d" % (hostname, port)
        return connection_string


def nano_to_str(elapsed):
    if elapsed is None:
        return None
    s = elapsed / 1000000000.0
    m, s = divmod(s, 60)
    h, m = divmod(m, 60)
    d, h = divmod(h, 24)
    elapsed_str = ' %fs' % s
    if m:
        elapsed_str = '%dm ' % m + elapsed_str
    if h:
        elapsed_str = '%dh ' % h + elapsed_str
    if d:
        elapsed_str = '%dd ' % d + elapsed_str
    return elapsed_str


class Queries(MyriaPage):
    def get(self, connection_=None):
        conn = connection_ or connection
        try:
            limit = int(self.request.get('limit', QUERIES_PER_PAGE))
            max_ = self.request.get('max', None)
            count, queries = conn.queries(limit, max_)
            if max_:
                max_ = int(max_)
            else:
                max_ = count
        except myria.MyriaError:
            queries = []

        for q in queries:
            q['elapsedStr'] = nano_to_str(q['elapsedNanos'])
            if q['status'] in ['ERROR', 'KILLED']:
                q['bootstrapStatus'] = 'danger'
            elif q['status'] == 'SUCCESS':
                q['bootstrapStatus'] = 'success'
            elif q['status'] == 'RUNNING':
                q['bootstrapStatus'] = 'warning'
            else:
                q['bootstrapStatus'] = ''

        template_vars = {'queries': queries,
                         'prevUrl': None,
                         'nextUrl': None}

        if queries:
            page = int(math.ceil(count - max_) / limit) + 1
            args = {arg: self.request.get(arg)
                    for arg in self.request.arguments()
                    if arg != 'page'}

            def page_url(page, current_max, pagination):
                largs = copy.copy(args)
                if page > 0:
                    largs['max'] = (current_max +
                                    (pagination.page - page) * limit)
                else:
                    largs.pop("max", None)
                return '{}?{}'.format(
                    self.request.path, urllib.urlencode(largs))

            template_vars['pagination'] = Pagination(
                page, limit, count)
            template_vars['current_max'] = max_
            template_vars['page_url'] = page_url
        else:
            template_vars['current_max'] = 0
            template_vars['pagination'] = Pagination(
                1, limit, 0)

        # Actually render the page: HTML content
        self.response.headers['Content-Type'] = 'text/html'
        # .. connection string
        template_vars['connectionString'] = self.get_connection_string()
        # .. load and render the template
        template = JINJA_ENVIRONMENT.get_template('queries.html')
        self.response.out.write(template.render(template_vars))


class Profile(MyriaPage):
    def get(self, connection_):
        conn = connection_ or connection
        query_id = self.request.get("queryId")
        query_plan = {}
        if query_id != '':
            try:
                query_plan = conn.get_query_status(query_id)
            except myria.MyriaError:
                pass

        template_vars = {
            'queryId': query_id,
            'myriaConnection': "%s:%d" % (hostname, port),
            'queryPlan': json.dumps(query_plan)
        }

        # Actually render the page: HTML content
        self.response.headers['Content-Type'] = 'text/html'
        # .. connection string
        template_vars['connectionString'] = self.get_connection_string(conn)
        # .. load and render the template
        template = JINJA_ENVIRONMENT.get_template('visualization.html')
        self.response.out.write(template.render(template_vars))


class Datasets(MyriaPage):
    def get(self, connection_=None):
        conn = connection_ or connection
        if not conn:
            datasets = []
        else:
            try:
                datasets = conn.datasets()
            except myria.MyriaError:
                datasets = []

        for d in datasets:
            try:
                d['queryUrl'] = 'http://%s:%d/query/query-%d' % (hostname, port, d['queryId'])
            except:
                pass

        template_vars = {'datasets': datasets}

        # Actually render the page: HTML content
        self.response.headers['Content-Type'] = 'text/html'
        # .. connection string
        template_vars['connectionString'] = self.get_connection_string(conn)
        # .. load and render the template
        template = JINJA_ENVIRONMENT.get_template('datasets.html')
        self.response.out.write(template.render(template_vars))


class Examples(MyriaPage):
    def get(self):
        # Get the language
        language = self.request.get('language')
        if not language:
            # default to Datalog
            language = 'datalog'
        else:
            language = language.strip().lower()
        # Is language recognized?
        if language not in examples:
            self.response.headers['Content-Type'] = 'text/plain'
            self.response.status = 404
            self.response.write('Error 404 (Not Found): language %s not found' % language)
            return
        # Return the objects as json
        self.response.headers['Content-Type'] = 'application/json'
        self.response.write(json.dumps(examples[language]))


class Editor(MyriaPage):
    def get(self, query=defaultquery, connection_=None):
        conn = connection_ or connection
        # Actually render the page: HTML content
        self.response.headers['Content-Type'] = 'text/html'
        template_vars = {}
        # .. pass in the query
        template_vars['query'] = query
        # .. pass in the Datalog examples to start
        template_vars['examples'] = examples['datalog']
        # .. connection string
        template_vars['connectionString'] = self.get_connection_string(conn)
        # .. load and render the template
        template = JINJA_ENVIRONMENT.get_template('editor.html')
        self.response.out.write(template.render(template_vars))


class Plan(MyriaHandler):
    def post(self):
        "The same as get(), here because there may be long programs"
        self.get()

    def get(self):
        self.response.headers.add_header("Access-Control-Allow-Origin", "*")
        query = self.request.get("query")
        language = self.request.get("language")
        try:
            plan = get_logical_plan(query, language)
        except (MyrialCompileException, MyrialInterpreter.NoSuchRelationException) as e:
            self.response.headers['Content-Type'] = 'text/plain'
            self.response.write(str(e))
            self.response.status = 400
            return

        self.response.headers['Content-Type'] = 'application/json'
        self.response.write(json.dumps(format_rule(plan)))


class Optimize(MyriaHandler):
    def get(self):
        self.response.headers.add_header("Access-Control-Allow-Origin", "*")
        query = self.request.get("query")
        language = self.request.get("language")
        try:
            optimized = get_physical_plan(query, language)
        except MyrialInterpreter.NoSuchRelationException as e:
            self.response.headers['Content-Type'] = 'text/plain'
            self.response.write("Error 400 (Bad Request): Relation %s not found" % str(e))
            self.response.status = 400
            return

        self.response.headers['Content-Type'] = 'application/json'
        self.response.write(json.dumps(format_rule(optimized)))

    def post(self):
        "The same as get(), here because there may be long programs"
        self.get()

class Compile(MyriaHandler):
    def get(self, connection_=None):
        self.response.headers.add_header("Access-Control-Allow-Origin", "*")
        conn = connection_ or connection
        query = self.request.get("query")
        language = self.request.get("language")

        cached_logicalplan = str(get_logical_plan(query, language))

        # Generate physical plan
        physicalplan = get_physical_plan(query, language)

        # Get the Catalog needed to get schemas for compiling the query
        try:
            catalog = MyriaCatalog(conn)
        except myria.MyriaError:
            catalog = None
        # .. and compile
        compiled = compile_to_json(query, cached_logicalplan, physicalplan, catalog)

        self.response.headers['Content-Type'] = 'application/json'
        self.response.write(json.dumps(compiled))

    def post(self):
        "The same as get(), here because there may be long programs"
        self.get()


class Execute(MyriaHandler):
    def post(self, connection_=None):
        self.response.headers.add_header("Access-Control-Allow-Origin", "*")
        conn = connection_ or connection
        if not conn:
            self.response.headers['Content-Type'] = 'text/plain'
            self.response.write("Error 503 (Service Unavailable): Unable to connect to REST server to issue query")
            self.response.status = 503
            return

        query = self.request.get("query")
        language = self.request.get("language")

        cached_logicalplan = str(get_logical_plan(query, language))

        # Generate physical plan
        physicalplan = get_physical_plan(query, language)

        # Get the Catalog needed to get schemas for compiling the query
        catalog = MyriaCatalog(conn)
        # .. and compile
        compiled = compile_to_json(query, cached_logicalplan, physicalplan, catalog)

        # Issue the query
        try:
            query_status = conn.submit_query(compiled)
            query_url = 'http://%s:%d/execute?query_id=%d' % (hostname, port, query_status['queryId'])
            ret = {'queryStatus': query_status, 'url': query_url}
            self.response.status = 201
            self.response.headers['Content-Type'] = 'application/json'
            self.response.headers['Content-Location'] = query_url
            self.response.write(json.dumps(ret))
            return
        except myria.MyriaError as e:
            self.response.headers['Content-Type'] = 'text/plain'
            self.response.status = 400
            self.response.write("Error 400 (Bad Request): %s" % str(e))
            return

    def get(self, connection_=None):
        self.response.headers.add_header("Access-Control-Allow-Origin", "*")
        conn = connection_ or connection
        if not conn:
            self.response.headers['Content-Type'] = 'text/plain'
            self.response.status = 503
            self.response.write("Error 503 (Service Unavailable): Unable to connect to REST server to issue query")
            return

        query_id = self.request.get("queryId")

        try:
            query_status = conn.get_query_status(query_id)
            self.response.headers['Content-Type'] = 'application/json'
            ret = {'queryStatus': query_status, 'url': self.request.url}
            self.response.write(json.dumps(ret))
        except myria.MyriaError as e:
            self.response.headers['Content-Type'] = 'text/plain'
            self.response.write(e)

class Dot(MyriaHandler):
    def get(self):
        self.response.headers.add_header("Access-Control-Allow-Origin", "*")
        query = self.request.get("query")
        language = self.request.get("language")
        plan_type = self.request.get("type")

        plan = get_plan(query, language, plan_type)

        self.response.headers['Content-Type'] = 'text/plain'
        self.response.write(get_dot(plan))

    def post(self):
        "The same as get(), here because there may be long programs"
        self.get()

app = webapp2.WSGIApplication(
    [
        ('/', RedirectToEditor),
        ('/editor', Editor),
        ('/queries', Queries),
        ('/profile', Profile),
        ('/datasets', Datasets),
        ('/plan', Plan),
        ('/optimize', Optimize),
        ('/compile', Compile),
        ('/execute', Execute),
        ('/dot', Dot),
        ('/examples', Examples),
    ],
    debug=True
)
