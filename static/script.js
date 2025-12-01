const allMessages = Array.from(document.querySelectorAll("[data-msg-idx]"));
let filtered = allMessages.slice();
let ascending = true;
let currentPage = 1;
let pageSize = parseInt(document.getElementById("pageSizeInput").value, 10) || 100;

const searchBox = document.getElementById("searchBox");
const searchBtn = document.getElementById("searchBtn");
const clearSearchBtn = document.getElementById("clearSearchBtn");
const sortAscBtn = document.getElementById("sortAscBtn");
const sortDescBtn = document.getElementById("sortDescBtn");
const pageInfo = document.getElementById("pageInfo");
const prevBtn = document.getElementById("prevBtn");
const nextBtn = document.getElementById("nextBtn");
const jumpInput = document.getElementById("jumpInput");
const jumpBtn = document.getElementById("jumpBtn");
const pageSizeInput = document.getElementById("pageSizeInput");
const container = document.getElementById("messages");

function updatePageSize() {
    const val = parseInt(pageSizeInput.value, 10);
    if (!isNaN(val) && val > 0) {
        pageSize = val;
        currentPage = 1;
        updatePageInfo();
    }
}

function applySearchAndSort() {
    const q = searchBox.value.toLowerCase().trim();
    filtered = allMessages.filter(el => {
        if (!q) return true;
        return el.innerText.toLowerCase().includes(q);
    });

    filtered.sort((a, b) => {
        const ta = a.dataset.rawTs || "";
        const tb = b.dataset.rawTs || "";
        if (ta < tb) return ascending ? -1 : 1;
        if (ta > tb) return ascending ? 1 : -1;
        return 0;
    });

    // Rebuild DOM in sorted + filtered order
    container.innerHTML = "";
    filtered.forEach(el => container.appendChild(el));

    currentPage = 1;
    updatePageInfo();
}

function totalPages() {
    return Math.max(1, Math.ceil(filtered.length / pageSize));
}

function updatePageInfo() {
    const total = totalPages();
    if (filtered.length === 0) {
        pageInfo.textContent = "No messages match your filter.";
    } else {
        pageInfo.textContent = `Page ${currentPage} of ${total} (${filtered.length} messages)`;
    }
    prevBtn.disabled = currentPage <= 1;
    nextBtn.disabled = currentPage >= total;
}

function jumpToPage(pageNum) {
    const total = totalPages();
    if (pageNum < 1 || pageNum > total) return;
    currentPage = pageNum;
    updatePageInfo();

    const idx = (currentPage - 1) * pageSize;
    if (idx >= filtered.length) return;
    filtered[idx].scrollIntoView({ behavior: "smooth", block: "start" });
}

searchBtn.addEventListener("click", () => {
    applySearchAndSort();
});

clearSearchBtn.addEventListener("click", () => {
    searchBox.value = "";
    applySearchAndSort();
});

// Enter key in search box triggers search
searchBox.addEventListener("keydown", (e) => {
    if (e.key === "Enter") {
        applySearchAndSort();
    }
});

sortAscBtn.addEventListener("click", () => {
    ascending = true;
    sortAscBtn.classList.add("bg-gray-800", "text-white");
    sortDescBtn.classList.remove("bg-gray-800", "text-white");
    sortDescBtn.classList.add("bg-gray-300", "text-gray-800");
    sortAscBtn.classList.remove("bg-gray-300", "text-gray-800");
    applySearchAndSort();
});

sortDescBtn.addEventListener("click", () => {
    ascending = false;
    sortDescBtn.classList.add("bg-gray-800", "text-white");
    sortAscBtn.classList.remove("bg-gray-800", "text-white");
    sortAscBtn.classList.add("bg-gray-300", "text-gray-800");
    sortDescBtn.classList.remove("bg-gray-300", "text-gray-800");
    applySearchAndSort();
});

prevBtn.addEventListener("click", () => {
    jumpToPage(currentPage - 1);
});

nextBtn.addEventListener("click", () => {
    jumpToPage(currentPage + 1);
});

jumpBtn.addEventListener("click", () => {
    const val = parseInt(jumpInput.value, 10);
    if (!isNaN(val)) {
        jumpToPage(val);
    }
});

pageSizeInput.addEventListener("change", () => {
    updatePageSize();
});

// Initial render: full convo, chronological ascending
applySearchAndSort();