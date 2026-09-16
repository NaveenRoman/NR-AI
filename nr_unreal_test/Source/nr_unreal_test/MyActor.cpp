#include "MyActor.h"

AMyActor::AMyActor()
{
	PrimaryActorTick.bCanEverTick = true;
	Health = 100.0f;
}

void AMyActor::TakeDamage(float Amount)
{
	Health -= Amount;
}
