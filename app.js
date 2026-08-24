const cylinders = Array.from({ length: 16 }, (_, index) => ({ number: index + 1, score: index === 3 ? 64 : 91 + ((index * 3) % 8), problem: index === 3 }));
const grid = document.querySelector('#cylinderGrid');
const toast = document.querySelector('#toast');
let selectedCylinder = 4;

function showToast(message) {
  toast.textContent = message;
  toast.classList.add('visible');
  window.clearTimeout(showToast.timer);
  showToast.timer = window.setTimeout(() => toast.classList.remove('visible'), 2800);
}

function renderCylinders() {
  grid.innerHTML = cylinders.map((cylinder) => `
    <button class="cylinder-tile ${cylinder.problem ? 'problem' : ''} ${cylinder.number === selectedCylinder ? 'selected' : ''}" data-cylinder="${cylinder.number}">
      <span class="tile-top"><span class="tile-number">CYL. ${String(cylinder.number).padStart(2, '0')}</span><i class="tile-state"></i></span>
      <strong class="tile-label">${cylinder.problem ? 'Pompa wtryskowa' : 'W normie'}</strong>
      <span class="tile-meta">score ${cylinder.score}/100</span>
    </button>`).join('');

  grid.querySelectorAll('.cylinder-tile').forEach((tile) => {
    tile.addEventListener('click', () => {
      selectedCylinder = Number(tile.dataset.cylinder);
      document.querySelector('#cylinderHeading').textContent = String(selectedCylinder).padStart(2, '0');
      renderCylinders();
      showToast(`Wybrano cylinder ${String(selectedCylinder).padStart(2, '0')}`);
    });
  });
}

renderCylinders();

document.querySelector('#engineSelect').addEventListener('change', (event) => {
  document.querySelector('#engineHeading').textContent = event.target.value;
  showToast(`Załadowano dane ${event.target.value}`);
});
document.querySelector('#reportButton').addEventListener('click', () => showToast('Raport demonstracyjny przygotowany do eksportu'));
document.querySelector('#compareButton').addEventListener('click', (event) => {
  event.currentTarget.textContent = event.currentTarget.textContent.includes('Ukryj') ? '⇄ Porównaj' : '⇄ Ukryj porównanie';
  showToast('Włączono porównanie z profilem referencyjnym');
});
document.querySelector('#detailsButton').addEventListener('click', () => showToast('Analiza: pasmo 13–16 kHz, odchylenie +31%'));
document.querySelector('#actionButton').addEventListener('click', (event) => {
  event.currentTarget.innerHTML = 'Dodano do zlecenia serwisowego <span>✓</span>';
  showToast('Cylinder 04 dodany do zlecenia serwisowego');
});
document.querySelectorAll('.filter').forEach((button) => button.addEventListener('click', () => {
  document.querySelectorAll('.filter').forEach((item) => item.classList.remove('active'));
  button.classList.add('active');
  const onlyProblems = button.textContent.includes('Problemy');
  grid.querySelectorAll('.cylinder-tile').forEach((tile) => { tile.hidden = onlyProblems && !tile.classList.contains('problem'); });
}));
