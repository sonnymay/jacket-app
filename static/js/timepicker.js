document.addEventListener("DOMContentLoaded", function () {
    const timePickerTrigger = document.getElementById("timePickerTrigger");
    const timePickerModal = document.getElementById("timePickerModal");
    const selectedTimeEl = document.getElementById("selectedTime");
    const timeInput = document.getElementById("daily_reminder_time");

    const pickerHour = document.getElementById("pickerHour");
    const pickerMinute = document.getElementById("pickerMinute");
    const pickerAmPm = document.getElementById("pickerAmPm");
    const pickerCancel = document.getElementById("pickerCancel");
    const pickerOk = document.getElementById("pickerOk");

    // Initialize with current value if it exists
    if (timeInput.value) {
        selectedTimeEl.textContent = timeInput.value;
    }

    timePickerTrigger.addEventListener("click", () => {
        timePickerModal.style.display = "block";
    });

    pickerCancel.addEventListener("click", () => {
        timePickerModal.style.display = "none";
    });

    pickerOk.addEventListener("click", () => {
        let h = parseInt(pickerHour.value, 10);
        let m = parseInt(pickerMinute.value, 10);
        let ampm = pickerAmPm.value;

        if (isNaN(h) || h < 1 || h > 12) h = 7;
        if (isNaN(m) || m < 0 || m > 59) m = 0;

        const hStr = h < 10 ? "0" + h : "" + h;
        const mStr = m < 10 ? "0" + m : "" + m;
        const timeString = `${hStr}:${mStr} ${ampm}`;

        selectedTimeEl.textContent = timeString;
        timeInput.value = timeString;

        timePickerModal.style.display = "none";
    });

    window.addEventListener("click", (event) => {
        if (event.target === timePickerModal) {
            timePickerModal.style.display = "none";
        }
    });
});
