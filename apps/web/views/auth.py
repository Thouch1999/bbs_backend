from django.contrib import messages
from django.contrib.auth import authenticate, login, logout
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.translation import gettext as _

from apps.accounts.models import User
from apps.accounts.serializers import RegisterSerializer
from apps.accounts.services import (
    PasswordResetError,
    request_password_reset_code,
    verify_password_reset_code,
)
from apps.web.forms import (
    EmailAuthenticationForm,
    ForgotPasswordConfirmForm,
    ForgotPasswordRequestForm,
    RegisterForm,
)


def login_view(request):
    if request.user.is_authenticated:
        return redirect("web:home")

    form = EmailAuthenticationForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        email = form.cleaned_data["email"]
        password = form.cleaned_data["password"]
        user = authenticate(request, username=email, password=password)
        if user is None:
            form.add_error(None, _("Incorrect email or password."))
        elif not user.is_active:
            form.add_error(None, _("This account has been locked."))
        else:
            login(request, user)
            messages.success(request, _("Welcome back."))
            next_url = request.GET.get("next") or "web:home"
            return redirect(next_url)

    return render(request, "web/auth/login.html", {"form": form})


def logout_view(request):
    logout(request)
    messages.success(request, _("You have been logged out."))
    return redirect("web:home")


def register_view(request):
    if request.user.is_authenticated:
        return redirect("web:home")

    form = RegisterForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        serializer = RegisterSerializer(
            data={
                "email": form.cleaned_data["email"],
                "full_name": form.cleaned_data["full_name"],
                "password": form.cleaned_data["password"],
            }
        )
        if serializer.is_valid():
            user = serializer.save()
            login(request, user)
            messages.success(request, _("Account created."))
            return redirect("web:profile")
        for field, errors in serializer.errors.items():
            target_field = field if field in form.fields else None
            for error in errors:
                form.add_error(target_field, str(error))

    return render(request, "web/auth/register.html", {"form": form})


def forgot_password_request_view(request):
    form = ForgotPasswordRequestForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        email = form.cleaned_data["email"]
        # Always behave the same whether or not the email has an account, so
        # this page can't be used to enumerate registered addresses — matches
        # apps.accounts.views.PasswordResetRequestView.
        if User.objects.filter(email__iexact=email).exists():
            request_password_reset_code(email)
        messages.success(request, _("If that account exists, a reset code was sent."))
        return redirect(f"{reverse('web:forgot_password_confirm')}?email={email}")

    return render(request, "web/auth/forgot_password_request.html", {"form": form})


def forgot_password_confirm_view(request):
    initial = {"email": request.GET.get("email", "")}
    form = ForgotPasswordConfirmForm(request.POST or None, initial=initial)
    if request.method == "POST" and form.is_valid():
        email = form.cleaned_data["email"]
        code = form.cleaned_data["code"]
        try:
            verify_password_reset_code(email, code)
        except PasswordResetError as exc:
            form.add_error("code", exc.message)
        else:
            try:
                user = User.objects.get(email__iexact=email)
            except User.DoesNotExist:
                messages.success(request, _("If that account exists, the password was reset."))
                return redirect("web:login")

            if not user.is_active:
                form.add_error(None, _("This account has been locked."))
            else:
                user.set_password(form.cleaned_data["new_password"])
                user.save(update_fields=["password"])
                messages.success(request, _("Password reset. You can now log in."))
                return redirect("web:login")

    return render(request, "web/auth/forgot_password_confirm.html", {"form": form})
