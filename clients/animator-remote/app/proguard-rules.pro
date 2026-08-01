#  NOCA -- Next Online Contest Administrator
#  Copyright (c) 2026 The NOCA Authors (see AUTHORS)
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

# Minification is off for the release build, so these rules are only a safety net
# for anyone who turns it on. kotlinx.serialization generates a companion
# `serializer()` per @Serializable class and looks it up reflectively.
-keepattributes *Annotation*, InnerClasses
-dontnote kotlinx.serialization.**

-keepclassmembers class org.noca.animator.remote.core.** {
    *** Companion;
}
-keepclasseswithmembers class org.noca.animator.remote.core.** {
    kotlinx.serialization.KSerializer serializer(...);
}
