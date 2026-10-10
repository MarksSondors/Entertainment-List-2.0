// Loaded by the GoAccess report (see goaccess.sh) before it renders: rename the
// generic panels to what this site actually feeds into them.
(function () {
  var ui = window.user_interface;
  if (!ui) {
    return;
  }
  if (ui.vhosts) {
    ui.vhosts.head = 'Page types';
    ui.vhosts.desc = 'Requests grouped by the Django route that handled them. '
      + 'Sorted by total time spent, so the top rows are what costs the server the most.';
  }
  if (ui.remote_user) {
    ui.remote_user.head = 'Signed-in users';
    ui.remote_user.desc = 'Requests per signed-in user (Stremio requests count for the API key owner).';
  }
})();
