import json
from pyramid import httpexceptions
from pyramid.response import Response
from wtforms import FileField

from wtforms_alchemy import ModelForm, model_form_factory

from pyramid_views.utils import ImproperlyConfigured, get_model_from_obj
from pyramid_views.views.base import TemplateResponseMixin, ContextMixin, View, MacroMixin
from pyramid_views.views.detail import (SingleObjectMixin,
                                        SingleObjectTemplateResponseMixin, BaseDetailView)


class FormMixin(ContextMixin):
    """
    A mixin that provides a way to show and handle a form in a request.
    """

    initial = {}
    form_class = None
    success_url = None
    prefix = None
    endpoint = False

    def get(self, request, *args, **kwargs):
        if self.endpoint and not self.template_name:
            return httpexceptions.HTTPNotImplemented()
        else:
            return super(FormMixin, self).get(request, *args, **kwargs)

    def get_initial(self):
        """
        Returns the initial data to use for forms on this view.
        """
        return self.initial.copy()

    def get_prefix(self):
        """
        Returns the prefix to use for forms on this view
        """
        return self.prefix

    def get_form_class(self):
        """
        Returns the form class to use in this view
        """
        return self.form_class

    def get_form(self, form_class):
        """
        Returns an instance of the form to be used in this view.
        """
        return form_class(**self.get_form_kwargs())

    def get_form_kwargs(self):
        """
        Returns the keyword arguments for instantiating the form.
        """
        kwargs = {
            'data': self.get_initial(),
            'prefix': self.get_prefix() or '',
            'obj': getattr(self, 'object', None),
        }

        if self.request.method.upper() in ('POST', 'PUT'):
            # Note that, unlike Django, Pyramid does not
            # distinguish file data from post data (Django
            # has both POST and FILES, Pyramid has just POST)
            kwargs.update({
                'formdata': self.request.POST,
            })
        return kwargs

    def get_success_url(self):
        """
        Returns the supplied success URL.
        """
        if self.success_url:
            # Forcing possible reverse_lazy evaluation
            url = self.success_url
        else:
            raise ImproperlyConfigured(
                "No URL to redirect to. Provide a success_url.")
        return url

    def form_valid(self, form):
        """
        If the form is valid, redirect to the supplied URL.
        """
        try:
            return httpexceptions.HTTPFound(self.get_success_url())
        except ImproperlyConfigured:
            if self.endpoint:
                # This is an endpoint, so we can just return an
                # empty response with status 200
                return Response('')
            else:
                raise

    def form_invalid(self, form):
        """
        If the form is invalid, re-render the context data with the
        data-filled form and errors.
        """
        if not self.endpoint:
            return self.render_to_response(self.get_context_data(form=form))
        else:
            # This is an endpoint, so return the errors as JSON
            response = httpexceptions.HTTPBadRequest()
            response.body = json.dumps({'errors': form.errors})
            return response


class ModelFormMixin(FormMixin, SingleObjectMixin):
    """
    A mixin that provides a way to show and handle a modelform in a request.
    """
    fields = None

    def get_form_class(self):
        """
        Returns the form class to use in this view.
        """
        if self.form_class:
            return self.form_class
        else:
            if self.model is not None:
                # If a model has been explicitly provided, use it
                model = self.model
            elif hasattr(self, 'object') and self.object is not None:
                # If this view is operating on a single object, use
                # the class of that object
                model = self.object.__class__
            else:
                # Try to get a query and extract the model class
                # from that
                model = get_model_from_obj(self.get_query())

            # Create a new class to use as the base. We do this to ensure
            # Meta.model is available when the form is generated by the factory.
            model_ = model
            class ModelFormWithModel(ModelForm):
                class Meta:
                    model = model_
                    only = self.fields
            model_form = model_form_factory(ModelFormWithModel)
            model_form.Meta.model = model
            return model_form

    def get_form_kwargs(self):
        """
        Returns the keyword arguments for instantiating the form.
        """
        kwargs = super(ModelFormMixin, self).get_form_kwargs()
        if hasattr(self, 'object'):
            kwargs.update({'instance': self.object})
        return kwargs

    def get_success_url(self):
        """
        Returns the supplied URL.
        """
        if self.success_url:
            url = self.success_url % self.object.__dict__
        else:
            try:
                url = self.object.get_absolute_url()
            except AttributeError:
                raise ImproperlyConfigured(
                    "No URL to redirect to.  Either provide a url or define"
                    " a get_absolute_url method on the Model.")
        return url

    def form_valid(self, form):
        """
        If the form is valid, save the associated model.
        """
        if self.object is None:
            model = self.model or self.get_form_class().Meta.model
            self.object = model()
        self.populate_obj(form)
        self.save()
        return super(ModelFormMixin, self).form_valid(form)

    def populate_obj(self, form):
        """ Populate ``self.object`` with the values from ``form``
        """
        form.populate_obj(self.object)

    def save(self):
        """
        Persist the model to the DB. Override this method
        if you need to alter the model pre or post save.
        """
        self.db_session.add(self.object)
        # Do a flush to ensure we get the primary key back
        self.db_session.flush()
        return self.object


class ProcessFormView(View):
    """
    A mixin that renders a form on GET and processes it on POST.
    """
    def get(self, request, *args, **kwargs):
        """
        Handles GET requests and instantiates a blank version of the form.
        """
        form_class = self.get_form_class()
        form = self.get_form(form_class)
        return self.render_to_response(self.get_context_data(form=form))

    def post(self, request, *args, **kwargs):
        """
        Handles POST requests, instantiating a form instance with the passed
        POST variables and then checked for validity.
        """
        form_class = self.get_form_class()
        form = self.get_form(form_class)
        if form.validate():
            return self.form_valid(form)
        else:
            return self.form_invalid(form)

    # PUT is a valid HTTP verb for creating (with a known URL) or editing an
    # object, note that browsers only support POST for now.
    def put(self, *args, **kwargs):
        return self.post(*args, **kwargs)


class BaseFormView(FormMixin, ProcessFormView):
    """
    A base view for displaying a form
    """


class FormView(TemplateResponseMixin, BaseFormView):
    """
    A view for displaying a form, and rendering a template response.
    """


class BaseCreateView(ModelFormMixin, ProcessFormView):
    """
    Base view for creating an new object instance.

    Using this base class requires subclassing to provide a response mixin.
    """
    def get(self, request, *args, **kwargs):
        self.object = None
        return super(BaseCreateView, self).get(request, *args, **kwargs)

    def post(self, request, *args, **kwargs):
        self.object = None
        return super(BaseCreateView, self).post(request, *args, **kwargs)


class CreateView(SingleObjectTemplateResponseMixin, MacroMixin, BaseCreateView):
    """
    View for creating a new object instance,
    with a response rendered by template.
    """
    template_name_suffix = '_form'


class BaseUpdateView(ModelFormMixin, ProcessFormView):
    """
    Base view for updating an existing object.

    Using this base class requires subclassing to provide a response mixin.
    """
    partial_updates = False

    def get(self, request, *args, **kwargs):
        self.object = self.get_object()
        return super(BaseUpdateView, self).get(request, *args, **kwargs)

    def post(self, request, *args, **kwargs):
        self.object = self.get_object()
        return super(BaseUpdateView, self).post(request, *args, **kwargs)

    def populate_obj(self, form):
        """ Populate ``self.object`` with the values from ``form``

        Supports doing partial updates if enabled via the
        ``partial_updates`` flag
        """
        if not self.partial_updates:
            form.populate_obj(self.object)
        else:
            for name, field in form._fields.items():
                # Only populate fields present in the post request
                # as this is a partial update.
                # Note we also exclude empty file upload fields, as it is
                # commonly desirable to leave a file field unchanged, rather than
                # blanking it out (and pre-populating a file field is not an option).
                in_post_data = name in self.request.POST
                empty_file_upload = isinstance(field, FileField) and field.data == ''
                always_update = name in getattr(self, 'always_update', [])
                if always_update or (in_post_data and not empty_file_upload):
                    field.populate_obj(self.object, name)

        post_populate = getattr(form, 'post_populate', None)
        if post_populate:
            form.post_populate(self.object)


class UpdateView(SingleObjectTemplateResponseMixin, MacroMixin, BaseUpdateView):
    """
    View for updating an object,
    with a response rendered by template.
    """
    template_name_suffix = '_form'


class DeletionMixin(object):
    """
    A mixin providing the ability to delete objects
    """
    success_url = None

    def delete(self, request, *args, **kwargs):
        """
        Calls the delete() method on the fetched object and then
        redirects to the success URL.
        """
        self.object = self.get_object()
        self.do_delete()
        try:
            success_url = self.get_success_url()
            return httpexceptions.HTTPFound(success_url)
        except ImproperlyConfigured:
            if getattr(self, 'endpoint', None):
                # This is an endpoint, so we can just return an
                # empty response with status 200
                return Response('')
            else:
                raise

    # Add support for browsers which only accept GET and POST for now.
    def post(self, request, *args, **kwargs):
        return self.delete(request, *args, **kwargs)

    def get_success_url(self):
        if self.success_url:
            return self.success_url % self.object.__dict__
        else:
            raise ImproperlyConfigured(
                "No URL to redirect to. Provide a success_url.")

    def do_delete(self):
        self.db_session.delete(self.object)


class BaseDeleteView(DeletionMixin, BaseDetailView):
    """
    Base view for deleting an object.

    Using this base class requires subclassing to provide a response mixin.
    """


class DeleteView(SingleObjectTemplateResponseMixin, MacroMixin, BaseDeleteView):
    """
    View for deleting an object retrieved with `self.get_object()`,
    with a response rendered by template.
    """
    template_name_suffix = '_confirm_delete'
