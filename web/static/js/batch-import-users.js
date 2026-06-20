// NOCA -- Next Online Contest Administrator
// Copyright (c) 2026 Daniel Correa Lobato <daniel@lobato.org>
// This program is distributed in the hope that it will be useful,
// but WITHOUT ANY WARRANTY; without even the implied warranty of
// MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

function validateBatchFile(form) {
  const input = form.querySelector('#file');
  const errDiv = form.querySelector('#file-error');
  const file = input.files[0];
  if (!file) return true;

  const name = file.name.toLowerCase();
  if (!name.endsWith('.csv') && !name.endsWith('.json')) {
    input.classList.add('is-invalid');
    errDiv.textContent = 'Only .csv and .json files are accepted.';
    return false;
  }
  if (file.size > 5 * 1024 * 1024) {
    input.classList.add('is-invalid');
    errDiv.textContent = 'File must be 5 MB or smaller.';
    return false;
  }

  document.getElementById('submit-btn').disabled = true;
  document.getElementById('spinner').classList.remove('d-none');
  return true;
}