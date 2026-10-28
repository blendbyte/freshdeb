Description: Debian has another location of "mysql.h".
Author: Francesco Paolo Lovergine <frankie@debian.org>
Forwarded: not needed

--- proftpd-dfsg-1.3.6b.orig/contrib/mod_sql_mysql.c
+++ proftpd-dfsg-1.3.6b/contrib/mod_sql_mysql.c
@@ -131,7 +131,7 @@
 #include "conf.h"
 #include "../contrib/mod_sql.h"
 
-#include <mysql.h>
+#include <mysql/mysql.h>
 #include <stdbool.h>
 
 /* The my_make_scrambled_password{,_323} functions are not part of the public
