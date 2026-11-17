# Changelog

The release workflow publishes the section for the new version as the GitHub Release notes, and the
in-app updater shows those notes when it offers the update. Keep each section short and written for you
as the user of the app.

## 1.2.0

- **App icon:** the Tutoring Organiser logo now shows in the window's title bar, the taskbar and every
  dialog, replacing the generic Windows icon.
- **Opens on the Overview:** the app now starts on the Overview page, with the graph set to
  Lesson activity over All time.
- **Graph hover:** hover over the Overview graph to see the period (x) and the value (y) of the nearest point.
  In a student's lesson cost vs payments graph, hovering a line shows the date and amount.
- **Online resources:** a new *Manage websites…* button lets you add, remove, rename, reorder and
  re-address the websites. You can also add the page that's open in the browser. The list is saved,
  and the app reopens the site you last used.
- **Expired Google sign-in:** when Google stops accepting the saved sign-in, *Settings* in the menu bar
  turns red and pulses slowly, and the Google account card in Settings is outlined in red with a
  *Connect Google* button. A message also appears the moment it happens. The alert clears as soon as you
  reconnect (or disconnect).
- **Fix:** the download message on Online resources was being written onto the My resources page.
  Each page now has its own message.
- **Fix:** the last month label on the Overview graph was cut off at the right edge.
