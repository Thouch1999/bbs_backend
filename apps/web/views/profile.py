from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.translation import gettext as _

from apps.accounts.models import SavedPassenger
from apps.web.forms import ProfileForm, SavedPassengerForm


@login_required
def profile_view(request):
    form = ProfileForm(request.POST or None, instance=request.user)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, _("Profile updated."))
        return redirect("web:profile")

    saved_passengers = request.user.saved_passengers.order_by("full_name")
    return render(
        request, "web/profile/profile.html", {"form": form, "saved_passengers": saved_passengers}
    )


@login_required
def saved_passenger_create_view(request):
    form = SavedPassengerForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        passenger = form.save(commit=False)
        passenger.user = request.user
        passenger.save()
        messages.success(request, _("Saved passenger added."))
        return redirect("web:profile")
    return render(request, "web/profile/saved_passenger_form.html", {"form": form})


@login_required
def saved_passenger_update_view(request, public_id):
    passenger = get_object_or_404(SavedPassenger, public_id=public_id, user=request.user)
    form = SavedPassengerForm(request.POST or None, instance=passenger)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, _("Saved passenger updated."))
        return redirect("web:profile")
    return render(request, "web/profile/saved_passenger_form.html", {"form": form})


@login_required
def saved_passenger_delete_view(request, public_id):
    passenger = get_object_or_404(SavedPassenger, public_id=public_id, user=request.user)
    if request.method == "POST":
        passenger.delete()
        messages.success(request, _("Saved passenger removed."))
        return redirect("web:profile")
    return render(request, "web/profile/saved_passenger_confirm_delete.html", {"passenger": passenger})
