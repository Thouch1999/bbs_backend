(function () {
    "use strict";

    var script = document.currentScript;
    var fareUsd = parseFloat(script.dataset.fareUsd || "0");
    var fareKhr = parseFloat(script.dataset.fareKhr || "0");
    var currency = script.dataset.currency || "KHR";
    var farePerSeat = currency === "USD" ? fareUsd : fareKhr;

    var selected = [];

    function formatMoney(amount) {
        if (currency === "USD") {
            return "$" + amount.toFixed(2);
        }
        return Math.round(amount).toLocaleString() + " KHR";
    }

    function render() {
        document.getElementById("id_seat_ids").value = selected.map(function (s) { return s.id; }).join(",");
        document.getElementById("bbms-fare-total").textContent = formatMoney(selected.length * farePerSeat);
        document.getElementById("bbms-continue-btn").disabled = selected.length === 0;

        var list = document.getElementById("bbms-selected-list");
        list.innerHTML = "";
        selected.forEach(function (s) {
            var li = document.createElement("li");
            li.textContent = s.number;
            list.appendChild(li);
        });
    }

    document.querySelectorAll(".bbms-seat--available").forEach(function (button) {
        button.addEventListener("click", function () {
            var id = button.dataset.seatId;
            var number = button.dataset.seatNumber;
            var index = selected.findIndex(function (s) { return s.id === id; });

            if (index === -1) {
                selected.push({ id: id, number: number });
                button.classList.add("bbms-seat--selected");
            } else {
                selected.splice(index, 1);
                button.classList.remove("bbms-seat--selected");
            }
            render();
        });
    });

    render();
})();
